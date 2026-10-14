from odoo import api, fields, models

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


class SmartInventoryConfig(models.AbstractModel):
    """Typed access to the module's ir.config_parameter settings."""
    _name = "smart.inventory.config"
    _description = "Smart Inventory Settings Reader"

    DEFAULTS = {
        "forecast_horizon": 7,
        "ai_enabled": False,
        "ai_batch_size": 50,
        "ai_daily_limit": 500,
        "replenish_mode": "po",
        "service_level": 95.0,
        "service_level_a": 98.0,
        "service_level_b": 95.0,
        "service_level_c": 90.0,
        "target_cover_days": 14,
        "po_approval_amount": 0.0,
        "dead_stock_days": 90,
        "expiry_alert_days": 30,
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
