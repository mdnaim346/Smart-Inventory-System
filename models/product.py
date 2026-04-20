from odoo import models, fields, api

class ProductTemplate(models.Model):
    _inherit = "product.template"

    low_stock_threshold = fields.Integer(string="Low Stock Alert", default=10)
    is_low_stock = fields.Boolean(compute="_compute_low_stock")

    @api.depends("qty_available", "low_stock_threshold")
    def _compute_low_stock(self):
        for rec in self:
            rec.is_low_stock = rec.qty_available <= rec.low_stock_threshold
            