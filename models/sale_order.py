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
        # Try to find a specific vendor, fallback to the first available partner
        vendor = self.env['res.partner'].search([('name', '=', 'Draft Vendor')], limit=1)
        if not vendor:
            vendor = self.env['res.partner'].search([], limit=1)
        
        if not vendor:
            return

        for order in self:
            for line in order.order_line:
                product = line.product_id
                if not product or product.detailed_type != 'product':
                    continue
                
                # Use forecasted quantity (virtual_available) to trigger PO immediately
                if product.virtual_available <= product.low_stock_threshold:
                    # Prevent duplicate POs for the same Sale Order and Product
                    existing_po = self.env['purchase.order'].search([
                        ('state', '=', 'draft'),
                        ('origin', '=', order.name),
                        ('order_line.product_id', '=', product.id)
                    ])
                    
                    if not existing_po:
                        self.env["purchase.order"].create({
                            "partner_id": vendor.id,
                            "origin": order.name,
                            "order_line": [(0, 0, {
                                "product_id": product.id,
                                "product_qty": 50,
                                "price_unit": product.standard_price or 1.0,
                                "name": product.name,
                            })]
                        })