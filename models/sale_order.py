from odoo import models, fields, api

class SaleOrder(models.Model):
    _inherit = "sale.order"

    def action_confirm(self):
        """ Override confirm to check for low stock and create RFQs automatically. """
        res = super().action_confirm()
        self._check_low_stock_and_create_po()
        return res

    def write(self, vals):
        """ Hook into write to catch custom status changes from dashboards. """
        res = super().write(vals)
        # Check standard Odoo state 'sale' and any custom 'status' field set to 'confirmed'
        if vals.get('status') == 'confirmed' or vals.get('state') == 'sale':
            self._check_low_stock_and_create_po()
        return res

    def _check_low_stock_and_create_po(self):
        """ Creates a Purchase Order for products where forecasted stock is below threshold. """
        for order in self:
            for line in order.order_line:
                product = line.product_id
                if not product or product.detailed_type != 'product':
                    continue
                
                # Check if product is low stock and auto-restock is enabled
                if product.is_low_stock and product.auto_restock:
                    vendor = product._get_vendor()
                    if not vendor:
                        continue

                    # Prevent duplicate POs for the same Sale Order and Product
                    existing_po = self.env['purchase.order'].search([
                        ('state', '=', 'draft'),
                        ('origin', '=', 'Auto Restock'),
                        ('order_line.product_id', '=', product.id)
                    ], limit=1)
                    
                    if not existing_po:
                        po = self.env["purchase.order"].create({
                            "partner_id": vendor.id,
                            "origin": "Auto Restock",
                            "order_line": [(0, 0, {
                                "product_id": product.id,
                                "product_qty": product.restock_quantity,
                                "price_unit": product.standard_price or 1.0,
                                "name": product.name,
                                "date_planned": fields.Datetime.now(),
                            })]
                        })
                        # Create notification activity
                        product._create_notification_activity(po)