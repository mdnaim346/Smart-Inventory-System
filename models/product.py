import json
import logging
import math
import random
import re
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import AccessError
from odoo.fields import Domain
from odoo.modules import module as odoo_module
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)

try:
    import anthropic
except ImportError:
    anthropic = None

SMART_ORIGIN = "Smart Restock"
# Origins written by earlier versions; still counted as smart restock POs.
LEGACY_ORIGINS = ("Auto Restock", "AI Smart Restock")
RISK_LEVELS = ("low", "medium", "high")
OPEN_RFQ_STATES = ("draft", "sent", "to approve")

AI_SYSTEM_PROMPT = (
    "You are a demand planner for a trading company. You receive one product's stock and "
    "a statistical demand baseline. Adjust the baseline only when the numbers justify it, "
    "assess the risk of running out of stock within the horizon, and explain it in one short "
    "sentence a store manager understands."
)
AI_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "prediction": {"type": "number"},
        "risk": {"type": "string", "enum": list(RISK_LEVELS)},
        "insight": {"type": "string"},
    },
    "required": ["prediction", "risk", "insight"],
    "additionalProperties": False,
}
# Models that accept server-side refusal fallbacks ("default" routing).
AI_FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-fable-5-2")
AI_TIMEOUT_SECONDS = 30.0
_AI_CLIENTS = {}


def _check_manager(env):
    if not env.su and not env.user.has_group("smart_inventory.group_smart_inventory_manager"):
        raise AccessError(_("Only Smart Inventory Managers can run this action."))


class ProductTemplate(models.Model):
    _inherit = "product.template"

    # =========================
    # CONFIG FIELDS
    # =========================
    low_stock_threshold = fields.Integer(
        string="Low Stock Alert",
        default=10,
        help="Minimum quantity before triggering restock (manual reorder mode)"
    )
    auto_restock = fields.Boolean(string="Enable Auto Restock", default=True)
    restock_quantity = fields.Integer(string="Restock Quantity", default=50)
    preferred_vendor_id = fields.Many2one(
        "res.partner",
        string="Preferred Vendor",
        domain=[("supplier_rank", ">", 0)]
    )
    reorder_mode = fields.Selection(
        [("manual", "Manual threshold"), ("auto", "Computed reorder point")],
        string="Reorder Rule", default="manual", required=True,
        help="Manual: restock below 'Low Stock Alert'.\n"
             "Computed: restock below demand during vendor lead time plus safety stock.")

    # =========================
    # STOCK STATUS (always live, issue #1)
    # =========================
    usable_qty = fields.Float(
        string="Usable Quantity", compute="_compute_stock_status", digits="Product Unit",
        help="On hand quantity minus expired lots.")
    is_low_stock = fields.Boolean(
        compute="_compute_stock_status", search="_search_is_low_stock")
    forecast_shortage = fields.Float(
        string="Forecast Shortage", compute="_compute_stock_status", search="_search_forecast_shortage")
    min_stock_level = fields.Float(
        string="Restock Below", compute="_compute_stock_status",
        help="Low Stock Alert in manual mode, computed reorder point otherwise.")
    days_of_cover = fields.Float(string="Days of Cover", compute="_compute_stock_status")
    si_stock_value = fields.Float(string="Stock Value", compute="_compute_stock_status")

    # =========================
    # FORECAST
    # =========================
    predicted_demand = fields.Float(
        string="Predicted Demand",
        help="Demand over the forecast horizon (default 7 days)."
    )
    avg_daily_demand = fields.Float(string="Avg Daily Demand", readonly=True)
    safety_stock = fields.Float(string="Safety Stock", readonly=True)
    reorder_point = fields.Float(string="Reorder Point", readonly=True)
    ai_insight = fields.Text(
        string="Business Insight",
        help="Advice based on sales trends and stock levels"
    )
    risk_level = fields.Selection([
        ('low', 'Low Risk'),
        ('medium', 'Medium Risk'),
        ('high', 'High Risk (Stockout Likely)')
    ], string="Risk Assessment", default='low')
    forecast_source = fields.Selection(
        [("ai", "AI"), ("baseline", "Baseline")], string="Forecast Source", readonly=True)
    last_ai_update = fields.Datetime(string="Last Forecast Update")
    forecast_ids = fields.One2many("smart.inventory.forecast", "product_tmpl_id", string="Forecast History")
    forecast_mape = fields.Float(string="Forecast Error (MAPE %)", compute="_compute_forecast_mape")

    # =========================
    # STOCK HEALTH
    # =========================
    abc_class = fields.Selection([("a", "A"), ("b", "B"), ("c", "C")], string="ABC Class", readonly=True)
    xyz_class = fields.Selection([("x", "X"), ("y", "Y"), ("z", "Z")], string="XYZ Class", readonly=True)
    last_sale_date = fields.Datetime(string="Last Sale", readonly=True)
    is_dead_stock = fields.Boolean(string="Dead Stock", readonly=True, index=True)

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends_context("warehouse_id")
    @api.depends("qty_available", "low_stock_threshold", "reorder_mode", "reorder_point",
                 "avg_daily_demand", "standard_price")
    def _compute_stock_status(self):
        expired = self.product_variant_ids._si_expired_qty()
        for rec in self:
            usable = rec.qty_available - sum(expired.get(v.id, 0.0) for v in rec.product_variant_ids)
            min_level = rec._si_min_level()
            rec.usable_qty = usable
            rec.min_stock_level = min_level
            storable = rec.is_storable
            rec.is_low_stock = storable and float_compare(usable, min_level, precision_digits=4) <= 0
            rec.forecast_shortage = max(0.0, min_level - usable) if storable else 0.0
            rec.days_of_cover = usable / rec.avg_daily_demand if rec.avg_daily_demand > 0 else 0.0
            rec.si_stock_value = max(rec.qty_available, 0.0) * rec.standard_price

    def _si_min_level(self):
        self.ensure_one()
        return self.reorder_point if self.reorder_mode == "auto" else float(self.low_stock_threshold)

    def _si_low_stock_ids(self):
        products = self.with_context(active_test=True).search([("is_storable", "=", True)])
        return products.filtered("is_low_stock").ids

    def _search_is_low_stock(self, operator, value):
        if operator not in ("in", "not in"):
            return NotImplemented
        low = Domain("id", "in", self._si_low_stock_ids())
        positive = True in value
        return low if (operator == "in") == positive else ~low

    def _search_forecast_shortage(self, operator, value):
        if operator not in (">", ">=", "<", "<=", "=", "in"):
            return NotImplemented
        products = self.with_context(active_test=True).search([("is_storable", "=", True)])
        values = value if operator == "in" else [value]
        ops = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b,
               "<=": lambda a, b: a <= b, "=": lambda a, b: a == b, "in": lambda a, b: a == b}
        match = products.filtered(lambda p: any(ops[operator](p.forecast_shortage, v) for v in values))
        return [("id", "in", match.ids)]

    @api.depends("forecast_ids.abs_pct_error", "forecast_ids.is_closed")
    def _compute_forecast_mape(self):
        engine = self.env["smart.inventory.forecast.engine"]
        for rec in self:
            closed = rec.forecast_ids.filtered("is_closed")[:30]
            rec.forecast_mape = engine._mape([(f.predicted, f.actual) for f in closed])

    # Restock settings only Smart Inventory Managers may change (E5.3).
    MANAGER_FIELDS = {"low_stock_threshold", "restock_quantity", "auto_restock",
                      "preferred_vendor_id", "reorder_mode"}

    def write(self, vals):
        if self and self.MANAGER_FIELDS & vals.keys():
            _check_manager(self.env)
            if vals.keys() <= self.MANAGER_FIELDS and not self.has_access("write"):
                # Managers may tune restock settings without full product edit rights
                self.check_access("read")
                return super(ProductTemplate, self.sudo()).write(vals)
        return super().write(vals)

    # =========================
    # PUBLIC ACTIONS
    # =========================
    def action_check_and_restock(self):
        """Refresh forecasts, then replenish low products (button, dashboard and cron).
        Called on an empty recordset, it processes every auto-restock product."""
        _check_manager(self.env)
        if not self:
            # Whole catalog: too slow for a web request, run the crons in the background.
            self.env.ref("smart_inventory.ir_cron_generate_ai_insights")._trigger()
            self.env.ref("smart_inventory.ir_cron_auto_restock")._trigger(
                fields.Datetime.now() + timedelta(minutes=1))
            return True
        self._si_update_forecasts()
        self.product_variant_ids._si_smart_restock()
        return True

    def action_generate_ai_insights(self):
        """Recompute demand forecast, risk and insight (baseline + optional AI)."""
        _check_manager(self.env)
        self._si_update_forecasts()
        return True

    def action_open_forecast_chart(self):
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": "smart_inventory_forecast_chart",
            "name": _("Forecast: %s", self.display_name),
            "params": {"product_tmpl_id": self.id},
        }

    # =========================
    # CRON ENTRY POINTS
    # =========================
    @api.model
    def _cron_auto_restock(self):
        """Restock every company's warehouses with the products that company uses."""
        companies = self.env["res.company"].search([])
        default_company = self.env.company
        for company in companies:
            env_company = self.with_company(company).with_context(allowed_company_ids=[company.id])
            templates = env_company.search([("auto_restock", "=", True), ("is_storable", "=", True)])
            templates = templates._si_used_by(company, default=company == default_company)
            templates.product_variant_ids._si_smart_restock()
        return True

    def _si_used_by(self, company, default=False):
        """Templates owned by `company`, or shared and moved/sold there.
        Shared templates with no activity anywhere belong to the `default` company."""
        variants = self.product_variant_ids
        Move, Line = self.env["stock.move"].sudo(), self.env["sale.order.line"].sudo()
        active_here = {p.id for [p] in Move._read_group([("product_id", "in", variants.ids),
                                                        ("company_id", "=", company.id)], ["product_id"])}
        active_here |= {p.id for [p] in Line._read_group([("product_id", "in", variants.ids),
                                                         ("company_id", "=", company.id)], ["product_id"])}
        active_any = set()
        if default:
            active_any = {p.id for [p] in Move._read_group([("product_id", "in", variants.ids)], ["product_id"])}
            active_any |= {p.id for [p] in Line._read_group([("product_id", "in", variants.ids)], ["product_id"])}
        return self.filtered(lambda t: t.company_id == company
                             or (not t.company_id and (set(t.product_variant_ids.ids) & active_here
                                                       or (default and not set(t.product_variant_ids.ids) & active_any))))

    @api.model
    def _cron_generate_insights(self):
        """Batch forecast refresh (E1.5): only products that changed, committed per batch."""
        config = self.env["smart.inventory.config"]
        horizon = config.get("forecast_horizon")
        batch_size = max(config.get("ai_batch_size"), 1)
        # All companies at once: shared products aggregate demand over every company.
        self = self.with_context(allowed_company_ids=self.env["res.company"].search([]).ids)
        todo = self.search([("is_storable", "=", True)])._si_needs_refresh(horizon)
        cron = self.env["ir.cron"]
        for start in range(0, len(todo), batch_size):
            batch = todo[start:start + batch_size]
            try:
                batch._si_update_forecasts()
            except Exception:
                _logger.exception("Smart Inventory: forecast batch failed (%s)", batch.ids)
                if odoo_module.current_test:
                    raise
                self.env.cr.rollback()
                continue
            if not odoo_module.current_test:
                cron._commit_progress(len(batch), remaining=len(todo) - start - len(batch))
        return True

    @api.model
    def _cron_update_stock_health(self):
        """Daily: last sale date and dead stock flag (E3.5)."""
        days = self.env["smart.inventory.config"].get("dead_stock_days")
        templates = self.search([("is_storable", "=", True)])
        groups = self.env["smart.inventory.forecast.engine"]._sale_report_groups(
            [("product_tmpl_id", "in", templates.ids), ("state", "=", "sale")],
            ["product_tmpl_id"], ["date:max"])
        last_sale = {tmpl.id: date for tmpl, date in groups}
        limit = fields.Datetime.now() - timedelta(days=days)
        for tmpl in templates:
            last = last_sale.get(tmpl.id)
            reference = last or tmpl.create_date
            dead = tmpl.qty_available > 0 and reference and reference < limit
            vals = {"last_sale_date": last or False, "is_dead_stock": bool(dead)}
            if tmpl.last_sale_date != vals["last_sale_date"] or tmpl.is_dead_stock != vals["is_dead_stock"]:
                tmpl.write(vals)
        return True

    @api.model
    def _cron_classify_abc_xyz(self):
        """Monthly ABC (sales value, 365 days) and XYZ (weekly demand variability, 13 weeks) (E3.4)."""
        engine = self.env["smart.inventory.forecast.engine"]
        templates = self.search([("is_storable", "=", True)])
        today = engine._today()
        value_groups = self.env["smart.inventory.forecast.engine"]._sale_report_groups(
            [("product_tmpl_id", "in", templates.ids), ("state", "=", "sale"),
             ("date", ">=", engine._day_start_utc(today - timedelta(days=365)))],
            ["product_tmpl_id"], ["price_subtotal:sum"])
        values = {t.id: 0.0 for t in templates}
        values.update({tmpl.id: amount for tmpl, amount in value_groups})
        abc = engine._abc(values)

        week_groups = self.env["smart.inventory.forecast.engine"]._sale_report_groups(
            [("product_tmpl_id", "in", templates.ids), ("state", "=", "sale"),
             ("date", ">=", engine._day_start_utc(today - timedelta(days=91)))],
            ["product_tmpl_id", "date:day"], ["product_uom_qty:sum"])
        weekly = {t.id: [0.0] * 13 for t in templates}
        for tmpl, day, qty in week_groups:
            index = min((today - fields.Date.to_date(day)).days // 7, 12)
            weekly[tmpl.id][index] += qty
        for tmpl in templates:
            tmpl.write({"abc_class": abc.get(tmpl.id, "c"), "xyz_class": engine._xyz(weekly[tmpl.id])})
        return True

    # =========================
    # FORECASTING (E1)
    # =========================
    def _si_needs_refresh(self, horizon):
        """Templates with sales or stock moves since their last update,
        never updated, or whose forecast is older than the horizon."""
        stale_limit = fields.Datetime.now() - timedelta(days=horizon)
        never = self.filtered(lambda t: not t.last_ai_update or t.last_ai_update < stale_limit)
        rest = self - never
        if not rest:
            return never
        variants = rest.product_variant_ids
        sale_groups = self.env["sale.order.line"].sudo()._read_group(
            [("product_id", "in", variants.ids)], ["product_id"], ["write_date:max"])
        move_groups = self.env["stock.move"].sudo()._read_group(
            [("product_id", "in", variants.ids), ("state", "=", "done")], ["product_id"], ["date:max"])
        last_change = {}
        for variant, date in sale_groups + move_groups:
            tmpl_id = variant.product_tmpl_id.id
            last_change[tmpl_id] = max(last_change.get(tmpl_id, date), date)
        changed = rest.filtered(lambda t: last_change.get(t.id) and last_change[t.id] > t.last_ai_update)
        return never | changed

    def _si_update_forecasts(self):
        config = self.env["smart.inventory.config"]
        engine = self.env["smart.inventory.forecast.engine"]
        horizon = config.get("forecast_horizon")
        templates = self.filtered("is_storable")
        variants = templates.product_variant_ids
        if not variants:
            return
        today = engine._today()
        sales = engine._daily_sales(variants, 90, end=today)
        warehouses = self.env["stock.warehouse"].search([("company_id", "in", self.env.companies.ids)])
        self.env["smart.inventory.stock.level"]._si_refresh(variants, warehouses, horizon)

        Forecast = self.env["smart.inventory.forecast"]
        for tmpl in templates:
            forecast = daily = variance = 0.0
            for variant in tmpl.product_variant_ids:
                stats = engine._baseline(sales.get(variant.id, {}), horizon, end=today)
                forecast += stats["forecast"]
                daily += stats["daily"]
                variance += stats["sigma"] ** 2
            sigma = math.sqrt(variance)
            lead_time = tmpl.product_variant_id._si_lead_time()
            service_level = tmpl._si_service_level()
            reorder_point, safety = engine._reorder_point(daily, sigma, lead_time, service_level)

            vals = {
                "predicted_demand": forecast,
                "avg_daily_demand": daily,
                "safety_stock": safety,
                "reorder_point": reorder_point,
                "forecast_source": "baseline",
                "last_ai_update": fields.Datetime.now(),
            }
            ai = tmpl._si_ai_forecast(forecast, daily, horizon)
            if ai:
                vals.update(predicted_demand=ai["prediction"], forecast_source="ai")
            # Computed results, not user edits: callers were already checked (manager or cron)
            tmpl.sudo().write(vals)
            risk, insight = tmpl._si_assess(horizon)
            if ai:
                risk, insight = ai["risk"], ai["insight"] or insight
            tmpl.sudo().write({"risk_level": risk, "ai_insight": insight})

            Forecast.create({
                "product_tmpl_id": tmpl.id,
                "product_id": tmpl.product_variant_id.id,
                "date": today,
                "horizon": horizon,
                "baseline": forecast,
                "predicted": vals["predicted_demand"],
                "source": vals["forecast_source"],
                "company_id": (tmpl.company_id or self.env.company).id,
            })

    def _si_service_level(self):
        self.ensure_one()
        config = self.env["smart.inventory.config"]
        if self.abc_class:
            return config.get("service_level_" + self.abc_class)
        return config.get("service_level")

    def _si_assess(self, horizon):
        """Risk level and a plain-language insight from the baseline numbers."""
        self.ensure_one()
        usable, demand, min_level = self.usable_qty, self.predicted_demand, self._si_min_level()
        if usable < demand:
            risk = "high"
        elif usable <= min_level or usable < demand + self.safety_stock:
            risk = "medium"
        else:
            risk = "low"
        if self.avg_daily_demand <= 0:
            return risk, _("No sales in the last 90 days.")
        cover = usable / self.avg_daily_demand
        if risk == "high":
            text = _("Expected demand of %(demand).0f in the next %(days)s days exceeds usable stock "
                     "(%(stock).0f); stock runs out in about %(cover).0f days.",
                     demand=demand, days=horizon, stock=usable, cover=cover)
        elif risk == "medium":
            text = _("Stock (%(stock).0f) is at or below the restock level (%(level).0f); "
                     "about %(cover).0f days of cover left.", stock=usable, level=min_level, cover=cover)
        else:
            text = _("Stock levels are healthy: about %(cover).0f days of cover at %(daily).1f units/day.",
                     cover=cover, daily=self.avg_daily_demand)
        return risk, text

    # ----- AI hook (provider integration is issue E1.1) -----
    def _si_ai_forecast(self, baseline, daily, horizon):
        """Ask the AI provider to adjust the baseline. Returns parsed dict or None."""
        self.ensure_one()
        config = self.env["smart.inventory.config"]
        if not config.get("ai_enabled") or not self._si_ai_quota_left():
            return None
        prompt = (
            f"Product: {self.name}\n"
            f"Usable stock: {self.usable_qty:.2f}\n"
            f"Average daily demand (weighted 7/30/90 days): {daily:.2f}\n"
            f"Statistical baseline for the next {horizon} days: {baseline:.2f}\n"
            "Adjust the baseline if the data suggests it, and assess stockout risk.\n"
            'Reply with JSON only: {"prediction": <number>, "risk": "low|medium|high", '
            '"insight": "<one sentence>"}'
        )
        try:
            response = self._ask_ai(prompt)
        except Exception:
            _logger.warning("Smart Inventory: AI call failed for %s", self.display_name, exc_info=True)
            return None
        if response is None:
            return None
        self._si_ai_count_call()
        # OLD: returned the parsed reply as is, so the AI could move the forecast without limit
        # return self._parse_ai_response(response)
        parsed = self._parse_ai_response(response)
        if parsed:
            parsed["prediction"] = self._si_clamp_ai_prediction(parsed["prediction"], baseline)
        return parsed

    @api.model
    def _si_clamp_ai_prediction(self, prediction, baseline):
        """Keep the AI forecast within +/- the configured % of a non-zero baseline."""
        limit = self.env["smart.inventory.config"].get("ai_max_adjust_pct")
        if baseline <= 0 or limit <= 0:
            return prediction
        low, high = baseline * (1 - limit / 100.0), baseline * (1 + limit / 100.0)
        return min(max(prediction, max(low, 0.0)), high)

    # OLD: stub kept until a provider was connected (E1.1)
    # def _ask_ai(self, prompt):
    #     """Send `prompt` to the configured AI provider and return its text reply.
    #
    #     Returns None while no provider is connected; forecasts then use the
    #     statistical baseline. Provider integration is tracked in issue E1.1.
    #     """
    #     return None

    def _ask_ai(self, prompt):
        """Send `prompt` to Claude and return the JSON text reply, or None.

        None (baseline is kept) when the `anthropic` package is missing, the
        model refuses, or the reply is cut off. API errors propagate to the
        caller, which logs them and keeps the baseline.
        """
        client = self._si_ai_client()
        if client is None:
            return None
        model = self.env["smart.inventory.config"].get("ai_model")
        params = {
            "model": model,
            "max_tokens": 4096,
            "system": AI_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {
                "effort": "low",
                "format": {"type": "json_schema", "schema": AI_RESPONSE_SCHEMA},
            },
        }
        if model in AI_FALLBACK_MODELS:
            response = client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params)
        else:
            response = client.messages.create(**params)
        if response.stop_reason in ("refusal", "max_tokens"):
            _logger.warning("Smart Inventory: AI reply for %s stopped (%s)", self.display_name, response.stop_reason)
            return None
        return next((block.text for block in response.content if block.type == "text"), None)

    @api.model
    def _si_ai_client(self):
        """Anthropic client for the key in Settings (or the server's own credentials)."""
        if anthropic is None:
            _logger.warning("Smart Inventory: AI is enabled but the 'anthropic' Python package is not installed.")
            return None
        key = self.env["smart.inventory.config"].get("ai_api_key") or None
        if key not in _AI_CLIENTS:
            _AI_CLIENTS.clear()
            _AI_CLIENTS[key] = anthropic.Anthropic(api_key=key, timeout=AI_TIMEOUT_SECONDS, max_retries=1)
        return _AI_CLIENTS[key]

    @api.model
    def _parse_ai_response(self, text):
        """Parse and validate the AI JSON reply (E1.2). Returns None when invalid."""
        if not isinstance(text, str):
            return None
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            _logger.warning("Smart Inventory: AI reply has no JSON object: %.200s", text)
            return None
        try:
            data = json.loads(match.group(0))
        except ValueError:
            _logger.warning("Smart Inventory: AI reply is not valid JSON: %.200s", text)
            return None
        prediction, risk, insight = data.get("prediction"), data.get("risk"), data.get("insight", "")
        if isinstance(prediction, bool) or not isinstance(prediction, (int, float)) \
                or not math.isfinite(prediction) or prediction < 0:
            _logger.warning("Smart Inventory: invalid AI prediction %r", prediction)
            return None
        risk = str(risk).strip().lower() if risk is not None else ""
        if risk not in RISK_LEVELS:
            _logger.warning("Smart Inventory: invalid AI risk %r", data.get("risk"))
            return None
        return {"prediction": float(prediction), "risk": risk,
                "insight": str(insight or "").strip()[:500]}

    def _si_ai_quota_left(self):
        config = self.env["smart.inventory.config"]
        used = self._si_ai_calls_today()
        return used < config.get("ai_daily_limit")

    def _si_ai_calls_today(self):
        raw = self.env["ir.config_parameter"].sudo().get_param("smart_inventory.ai_calls", "")
        day, _sep, count = raw.partition(":")
        return int(count) if day == str(fields.Date.context_today(self)) and count.isdigit() else 0

    def _si_ai_count_call(self):
        today = str(fields.Date.context_today(self))
        self.env["ir.config_parameter"].sudo().set_param(
            "smart_inventory.ai_calls", f"{today}:{self._si_ai_calls_today() + 1}")

    # =========================
    # DEMO DATA
    # =========================
    @api.model
    def _si_demo_load_history(self):
        """Demo only: 60 days of confirmed sales so forecasts, KPIs and charts have data."""
        daily = {
            "smart_inventory.demo_product_rice": 6.0,
            "smart_inventory.demo_product_oil": 9.0,
            "smart_inventory.demo_product_biscuit": 1.5,
            "smart_inventory.demo_product_lentil": 4.0,
        }
        templates = self.browse([self.env.ref(xmlid).id for xmlid in daily])
        customer = self.env.ref("base.res_partner_2", raise_if_not_found=False) \
            or self.env["res.partner"].create({"name": "Demo Customer"})
        rng = random.Random(19)
        now = fields.Datetime.now()
        for days_ago in range(60, 0, -1):
            weekend = (now - timedelta(days=days_ago)).weekday() in (4, 5)
            lines = []
            for xmlid, avg in daily.items():
                qty = round(max(rng.gauss(avg * (1.4 if weekend else 1.0), avg * 0.3), 0.0))
                if qty:
                    lines.append((0, 0, {"product_id": self.env.ref(xmlid).product_variant_id.id,
                                         "product_uom_qty": qty}))
            if lines:
                order = self.env["sale.order"].create({"partner_id": customer.id, "order_line": lines})
                order.write({"state": "sale", "date_order": now - timedelta(days=days_ago)})
        templates._si_update_forecasts()
        return True

    # =========================
    # HELPERS
    # =========================
    def _get_vendor(self):
        """Preferred vendor, else the best supplier line (price, validity)."""
        self.ensure_one()
        return self.product_variant_id._get_vendor()

    def _create_notification_activity(self, purchase_order):
        purchase_order._si_notify_created()


class ProductProduct(models.Model):
    _inherit = "product.product"

    # ------------------------------------------------------------------
    # Quantities
    # ------------------------------------------------------------------
    def _si_expired_qty(self, warehouse=None):
        """{product_id: qty in expired lots} in internal locations (E3.3).
        Empty when lots have no expiration date (product_expiry not installed)."""
        if not self or "expiration_date" not in self.env["stock.lot"]._fields:
            return {}
        domain = [
            ("product_id", "in", self.ids),
            ("location_id.usage", "=", "internal"),
            ("lot_id.expiration_date", "!=", False),
            ("lot_id.expiration_date", "<=", fields.Datetime.now()),
        ]
        warehouse = warehouse or self.env["stock.warehouse"].browse(self.env.context.get("warehouse_id"))
        if warehouse:
            domain.append(("location_id", "child_of", warehouse.view_location_id.id))
        groups = self.env["stock.quant"].sudo()._read_group(domain, ["product_id"], ["quantity:sum"])
        return {product.id: qty for product, qty in groups}

    def _si_open_rfq_qty(self, warehouse):
        """{product_id: qty} on draft/sent RFQs for this warehouse (not yet in virtual_available)."""
        groups = self.env["purchase.order.line"].sudo()._read_group([
            ("product_id", "in", self.ids),
            ("order_id.state", "in", OPEN_RFQ_STATES),
            ("order_id.picking_type_id.warehouse_id", "=", warehouse.id),
        ], ["product_id"], ["product_uom_qty:sum"])
        return {product.id: qty for product, qty in groups}

    # ------------------------------------------------------------------
    # Vendor selection (E2.3) and order quantity (E2.2)
    # ------------------------------------------------------------------
    def _si_seller(self, quantity=1.0):
        self.ensure_one()
        preferred = self.product_tmpl_id.preferred_vendor_id
        today = fields.Date.context_today(self)
        partner = preferred or False
        # Prefer lines valid for this quantity; otherwise any valid line (its MOQ then raises the qty).
        return self._select_seller(partner_id=partner, quantity=quantity, date=today, uom_id=self.uom_id) \
            or self._select_seller(partner_id=partner, quantity=None, date=today, uom_id=self.uom_id)

    def _get_vendor(self, quantity=1.0):
        self.ensure_one()
        preferred = self.product_tmpl_id.preferred_vendor_id
        if preferred:
            return preferred
        return self._si_seller(quantity).partner_id

    def _si_lead_time(self):
        self.ensure_one()
        seller = self._si_seller()
        return max(seller.delay if seller else 1, 1)

    def _si_round_qty(self, qty, seller):
        """Round up to vendor minimum quantity and pack multiple (product UoM)."""
        if not seller:
            return qty
        moq = seller.product_uom_id._compute_quantity(seller.min_qty, self.uom_id) \
            if seller.product_uom_id else seller.min_qty
        qty = max(qty, moq)
        pack = seller.si_pack_multiple
        if pack > 0:
            qty = math.ceil(round(qty / pack, 6)) * pack
        return qty

    # ------------------------------------------------------------------
    # Replenishment (E2, E3)
    # ------------------------------------------------------------------
    def _si_restock_need(self, level, position):
        """Return (qty to order, min level) for one warehouse; qty <= 0 means no need.
        `position` = forecast stock in the warehouse incl. open RFQs, minus expired lots."""
        self.ensure_one()
        tmpl = self.product_tmpl_id
        demand = level.forecast if level else tmpl.predicted_demand
        if tmpl.reorder_mode == "auto":
            min_level = level.reorder_point if level else tmpl.reorder_point
            if float_compare(position, min_level, precision_digits=4) > 0 and position >= demand:
                return 0.0, min_level
            cover_days = self.env["smart.inventory.config"].get("target_cover_days")
            daily = level.daily_demand if level else tmpl.avg_daily_demand
            safety = level.safety_stock if level else tmpl.safety_stock
            target = max(daily * cover_days + safety, min_level, demand)
            return target - position, min_level
        min_level = level.min_qty_override if level and level.min_qty_override else float(tmpl.low_stock_threshold)
        if float_compare(position, min_level, precision_digits=4) > 0 and position >= demand:
            return 0.0, min_level
        return max(float(tmpl.restock_quantity), demand - position, min_level - position), min_level

    def _si_smart_restock(self, warehouses=None):
        """Single replenishment path used by the cron, sale confirmation and dashboard (E2.4).

        For each storable, auto-restock product and warehouse below its restock level:
        transfer from a warehouse with excess (E3.2), update the reordering rule
        (orderpoint mode, E2.6) or add a line to the vendor's draft smart PO.
        Quantities are read per warehouse in batch to keep large catalogs fast.
        """
        config = self.env["smart.inventory.config"]
        mode = config.get("replenish_mode")
        products = self.filtered(lambda p: p.is_storable and p.product_tmpl_id.auto_restock
                                 and not p.product_tmpl_id.is_dead_stock)
        if not products:
            return self.env["purchase.order"]
        warehouses = warehouses or self.env["stock.warehouse"].search([("company_id", "=", self.env.company.id)])
        Level = self.env["smart.inventory.stock.level"]
        levels = {(lvl.product_id.id, lvl.warehouse_id.id): lvl for lvl in Level.search([
            ("product_id", "in", products.ids), ("warehouse_id", "in", warehouses.ids)])}
        touched = self.env["purchase.order"]
        for warehouse in warehouses:
            expired = products._si_expired_qty(warehouse)
            open_rfq = products._si_open_rfq_qty(warehouse)
            for product in products.with_context(warehouse_id=warehouse.id):
                position = product.virtual_available - expired.get(product.id, 0.0) + open_rfq.get(product.id, 0.0)
                level = levels.get((product.id, warehouse.id), Level)
                qty, min_level = product._si_restock_need(level, position)
                if float_compare(qty, 0.0, precision_digits=4) <= 0:
                    continue
                qty = Level._si_suggest_transfer(product, warehouse, qty)
                if float_compare(qty, 0.0, precision_digits=4) <= 0:
                    continue
                seller = product._si_seller(qty)
                qty = product._si_round_qty(qty, seller)
                if mode == "orderpoint":
                    product._si_update_orderpoint(warehouse, min_level, qty)
                    continue
                vendor = product.product_tmpl_id.preferred_vendor_id or seller.partner_id
                if not vendor:
                    _logger.info("Smart Inventory: no vendor for %s, skipping restock", product.display_name)
                    continue
                touched |= self.env["purchase.order"]._si_add_restock_line(vendor, warehouse, product, qty)
        touched._si_check_approval()
        return touched

    def _si_update_orderpoint(self, warehouse, min_qty, order_qty):
        self.ensure_one()
        Orderpoint = self.env["stock.warehouse.orderpoint"]
        orderpoint = Orderpoint.search([
            ("product_id", "=", self.id), ("warehouse_id", "=", warehouse.id),
            ("location_id", "=", warehouse.lot_stock_id.id)], limit=1)
        vals = {"product_min_qty": min_qty, "product_max_qty": min_qty + order_qty}
        if orderpoint:
            orderpoint.write(vals)
        else:
            Orderpoint.create(dict(vals, product_id=self.id, warehouse_id=warehouse.id,
                                   location_id=warehouse.lot_stock_id.id,
                                   company_id=warehouse.company_id.id, trigger="manual"))

    def _create_notification_activity(self, purchase_order):
        purchase_order._si_notify_created()

    def action_check_and_restock(self):
        return self.product_tmpl_id.action_check_and_restock()


class ProductSupplierinfo(models.Model):
    _inherit = "product.supplierinfo"

    si_pack_multiple = fields.Float(
        string="Pack Multiple", digits="Product Unit",
        help="Smart restock orders a multiple of this quantity (in the product's unit). 0 = no rounding.")
