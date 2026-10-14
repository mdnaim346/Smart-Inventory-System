from datetime import timedelta

from odoo import api, fields, models


class SmartInventoryForecast(models.Model):
    """One demand forecast run for a product (E1.4)."""
    _name = "smart.inventory.forecast"
    _description = "Smart Inventory Forecast"
    _order = "date desc, id desc"

    product_tmpl_id = fields.Many2one("product.template", required=True, ondelete="cascade", index=True)
    product_id = fields.Many2one("product.product", required=True, ondelete="cascade", index=True)
    warehouse_id = fields.Many2one("stock.warehouse", ondelete="cascade")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    date = fields.Date(required=True, default=fields.Date.context_today, index=True)
    horizon = fields.Integer(required=True, default=7, help="Forecast window in days, starting on Date.")
    date_end = fields.Date(compute="_compute_date_end", store=True)
    predicted = fields.Float(string="Predicted Demand")
    baseline = fields.Float(string="Baseline Demand")
    actual = fields.Float(string="Actual Demand")
    is_closed = fields.Boolean(string="Actual Known", help="Set once the forecast window has passed.")
    source = fields.Selection([("ai", "AI"), ("baseline", "Baseline")], required=True, default="baseline")
    abs_pct_error = fields.Float(string="Abs. % Error", compute="_compute_error", store=True, aggregator="avg")

    @api.depends("date", "horizon")
    def _compute_date_end(self):
        for rec in self:
            rec.date_end = rec.date + timedelta(days=rec.horizon) if rec.date else False

    @api.depends("predicted", "actual", "is_closed")
    def _compute_error(self):
        for rec in self:
            rec.abs_pct_error = (100.0 * abs(rec.predicted - rec.actual) / rec.actual
                                 if rec.is_closed and rec.actual else 0.0)

    @api.model
    def _cron_fill_actuals(self):
        """Fill `actual` for forecasts whose window has passed."""
        today = self.env["smart.inventory.forecast.engine"]._today()
        open_forecasts = self.search([("is_closed", "=", False), ("date_end", "<=", today)])
        for (date, date_end, warehouse), recs in open_forecasts.grouped(
                lambda r: (r.date, r.date_end, r.warehouse_id)).items():
            actuals = self._actual_demand(recs.product_tmpl_id, date, date_end, warehouse)
            for rec in recs:
                rec.write({"actual": actuals.get(rec.product_tmpl_id.id, 0.0), "is_closed": True})
        return True

    @api.model
    def _actual_demand(self, templates, date_from, date_to, warehouse=None):
        """{template_id: confirmed sales qty} with order date in [date_from, date_to)."""
        engine = self.env["smart.inventory.forecast.engine"]
        domain = [
            ("product_tmpl_id", "in", templates.ids),
            ("state", "=", "sale"),
            ("date", ">=", engine._day_start_utc(date_from)),
            ("date", "<", engine._day_start_utc(date_to)),
        ]
        if warehouse:
            domain.append(("warehouse_id", "=", warehouse.id))
        groups = engine._sale_report_groups(
            domain, ["product_tmpl_id"], ["product_uom_qty:sum"])
        return {tmpl.id: qty for tmpl, qty in groups}
