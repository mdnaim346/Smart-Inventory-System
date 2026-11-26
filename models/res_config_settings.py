from odoo import api, fields, models, _

PARAM = "smart_inventory."


class ResCompany(models.Model):
    _inherit = "res.company"

    # Sale automation toggles (E5.2). Defaults keep the original module behaviour.
    si_block_out_of_stock = fields.Boolean(default=True)
    si_auto_invoice = fields.Boolean(default=True)
    si_auto_deliver = fields.Boolean(default=True)
    si_create_backorder = fields.Boolean(default=True)
    si_digest_user_ids = fields.Many2many("res.users", "smart_inventory_digest_user_rel", "company_id", "user_id")


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # Sales flow
    si_block_out_of_stock = fields.Boolean(
        related="company_id.si_block_out_of_stock", readonly=False,
        string="Block Out-of-Stock Sales")
    si_auto_invoice = fields.Boolean(
        related="company_id.si_auto_invoice", readonly=False,
        string="Auto-Invoice Confirmed Sales")
    si_auto_deliver = fields.Boolean(
        related="company_id.si_auto_deliver", readonly=False,
        string="Auto-Validate Deliveries")
    si_create_backorder = fields.Boolean(
        related="company_id.si_create_backorder", readonly=False,
        string="Create Backorders",
        help="When stock blocking is off, deliver what is available and keep the rest as a backorder.")

    # Forecasting
    si_forecast_horizon = fields.Integer(
        string="Forecast Horizon (days)", default=7, config_parameter=PARAM + "forecast_horizon")
    si_ai_enabled = fields.Boolean(
        string="Use AI Provider", config_parameter=PARAM + "ai_enabled",
        help="When off (or no provider is connected), forecasts use the statistical baseline only.")
    si_ai_batch_size = fields.Integer(
        string="AI Batch Size", default=50, config_parameter=PARAM + "ai_batch_size")
    si_ai_daily_limit = fields.Integer(
        string="Max AI Calls per Day", default=500, config_parameter=PARAM + "ai_daily_limit")
    si_ai_api_key = fields.Char(
        string="Claude API Key", config_parameter=PARAM + "ai_api_key", groups="base.group_system",
        help="Anthropic API key. Leave empty to use the server's ANTHROPIC_API_KEY environment variable.")
    si_ai_model = fields.Char(
        string="Claude Model", default="claude-opus-5-5", config_parameter=PARAM + "ai_model")
    si_ai_max_adjust_pct = fields.Float(
        string="Max AI Adjustment (%)", default=50.0, config_parameter=PARAM + "ai_max_adjust_pct",
        help="The AI may move the statistical forecast up or down by at most this percentage. 0 = no limit.")
    si_ai_package_missing = fields.Boolean(compute="_compute_si_ai_package_missing")

    def _compute_si_ai_package_missing(self):
        from . import product
        self.si_ai_package_missing = product.anthropic is None

    # Replenishment
    si_replenish_mode = fields.Selection(
        [("po", "Create purchase orders"), ("orderpoint", "Update Odoo reordering rules")],
        string="Replenishment Mode", default="po", config_parameter=PARAM + "replenish_mode")
    si_service_level = fields.Float(
        string="Default Service Level (%)", default=95.0, config_parameter=PARAM + "service_level")
    si_service_level_a = fields.Float(string="Service Level A (%)", default=98.0, config_parameter=PARAM + "service_level_a")
    si_service_level_b = fields.Float(string="Service Level B (%)", default=95.0, config_parameter=PARAM + "service_level_b")
    si_service_level_c = fields.Float(string="Service Level C (%)", default=90.0, config_parameter=PARAM + "service_level_c")
    si_target_cover_days = fields.Integer(
        string="Target Days of Cover", default=14, config_parameter=PARAM + "target_cover_days")
    si_order_qty_mode = fields.Selection(
        [("cover", "Target days of cover"), ("eoq", "Economic order quantity (EOQ)")],
        string="Order Quantity", default="cover", config_parameter=PARAM + "order_qty_mode",
        help="Computed reorder mode: order up to the days of cover, or at least the economic order "
             "quantity that balances ordering and holding cost.")
    si_order_cost = fields.Float(
        string="Cost per Order", default=500.0, config_parameter=PARAM + "order_cost",
        help="Paperwork, transport and handling for one purchase order (company currency).")
    si_holding_cost_pct = fields.Float(
        string="Holding Cost (% per year)", default=20.0, config_parameter=PARAM + "holding_cost_pct",
        help="Yearly cost of keeping one unit, as a percentage of its cost: storage, capital, spoilage.")
    si_po_approval_amount = fields.Float(
        string="Approval Above Amount", config_parameter=PARAM + "po_approval_amount",
        help="Smart restock POs above this untaxed amount need a Smart Inventory Manager's approval. 0 disables approval.")

    # Stock health
    si_dead_stock_days = fields.Integer(
        string="Dead Stock After (days)", default=90, config_parameter=PARAM + "dead_stock_days")
    si_expiry_alert_days = fields.Integer(
        string="Expiry Alert Window (days)", default=30, config_parameter=PARAM + "expiry_alert_days")

    # Alerts
    si_digest_user_ids = fields.Many2many(
        related="company_id.si_digest_user_ids", readonly=False, string="Daily Digest Recipients")
    si_telegram_token = fields.Char(
        string="Telegram Bot Token", config_parameter=PARAM + "telegram_token", groups="base.group_system")
    si_telegram_chat_id = fields.Char(string="Telegram Chat ID", config_parameter=PARAM + "telegram_chat_id")
    si_alert_webhook_url = fields.Char(
        string="Alert Webhook URL", config_parameter=PARAM + "alert_webhook_url", groups="base.group_system",
        help="Alerts are POSTed here as JSON {\"text\": ...}. Use it for a WhatsApp or SMS gateway.")

    # Purchasing control
    si_restock_review = fields.Boolean(
        string="Review Suggestions Before Ordering", config_parameter=PARAM + "restock_review",
        help="Restock creates suggestions that a manager approves, edits or rejects, "
             "instead of adding lines to purchase orders directly.")
    si_weekly_budget = fields.Float(
        string="Weekly Restock Budget", config_parameter=PARAM + "weekly_budget",
        help="Untaxed amount of smart restock orders per week (company currency). "
             "Needs above the budget become suggestions. 0 = no budget.")
    si_overstock_days = fields.Integer(
        string="Overstock Above (days of cover)", default=120, config_parameter=PARAM + "overstock_days")

    # Counting
    si_cycle_count = fields.Boolean(
        string="ABC Cycle Counting", config_parameter=PARAM + "cycle_count",
        help="Schedule stock counts by ABC class: A products more often than C products.")
    si_cycle_days_a = fields.Integer(string="Count A Every (days)", default=30, config_parameter=PARAM + "cycle_days_a")
    si_cycle_days_b = fields.Integer(string="Count B Every (days)", default=90, config_parameter=PARAM + "cycle_days_b")
    si_cycle_days_c = fields.Integer(string="Count C Every (days)", default=180, config_parameter=PARAM + "cycle_days_c")
    si_require_adjust_reason = fields.Boolean(
        string="Require Adjustment Reason", config_parameter=PARAM + "require_adjust_reason",
        help="A stock count that changes the quantity must have a reason (damaged, expired, theft...).")
    si_fefo = fields.Boolean(
        string="First Expired, First Out", config_parameter=PARAM + "fefo",
        help="Pick lots that expire first for products with expiration dates (needs Product Expiry).")

    def set_values(self):
        super().set_values()
        if self.si_fefo:
            self.env["product.template"]._si_apply_fefo()

    def action_si_test_alert(self):
        self.execute()
        sent = self.env["smart.inventory.alert"]._send(_("Smart Inventory test alert from %s.", self.env.company.name))
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "type": "success" if sent else "warning",
            "message": _("Test alert sent.") if sent else _("No alert channel is configured, or sending failed."),
        }}


class SmartInventoryConfig(models.AbstractModel):
    """Typed access to the module's ir.config_parameter settings."""
    _name = "smart.inventory.config"
    _description = "Smart Inventory Settings Reader"

    DEFAULTS = {
        "forecast_horizon": 7,
        "ai_enabled": False,
        "ai_batch_size": 50,
        "ai_daily_limit": 500,
        "ai_api_key": "",
        "ai_model": "claude-opus-5-5",
        "ai_max_adjust_pct": 50.0,
        "replenish_mode": "po",
        "service_level": 95.0,
        "service_level_a": 98.0,
        "service_level_b": 95.0,
        "service_level_c": 90.0,
        "target_cover_days": 14,
        "po_approval_amount": 0.0,
        "order_qty_mode": "cover",
        "order_cost": 500.0,
        "holding_cost_pct": 20.0,
        "dead_stock_days": 90,
        "expiry_alert_days": 30,
        "telegram_token": "",
        "telegram_chat_id": "",
        "alert_webhook_url": "",
        "restock_review": False,
        "weekly_budget": 0.0,
        "overstock_days": 120,
        "cycle_count": False,
        "cycle_days_a": 30,
        "cycle_days_b": 90,
        "cycle_days_c": 180,
        "require_adjust_reason": False,
        "fefo": False,
    }

    @api.model
    def get(self, key):
        default = self.DEFAULTS[key]
        raw = self.env["ir.config_parameter"].sudo().get_param(PARAM + key)
        if raw in (None, False, ""):
            return default
        if isinstance(default, bool):
            return raw not in ("False", "0", "false")
        if isinstance(default, int):
            try:
                return int(float(raw))
            except ValueError:
                return default
        if isinstance(default, float):
            try:
                return float(raw)
            except ValueError:
                return default
        return raw
