from odoo import models

class SaleOrder(models.Model):
    _inherit = "sale.order"

    def action_confirm(self):
        res = super().action_confirm()

        # Optimization: Find a default vendor to avoid hardcoded IDs
        # In a real scenario, you'd use product.seller_ids[0].partner_id
        vendor = self.env['res.partner'].search([('name', '=', 'Draft Vendor')], limit=1)
        if not vendor:
            vendor = self.env['res.partner'].search([], limit=1) # Fallback to any partner

        for order in self:
            for line in order.order_line:
                product = line.product_id

                # Check stock on the template (inherited fields are accessible via variant)
                if product.qty_available <= product.low_stock_threshold:
                    self.env["purchase.order"].create({
                        "partner_id": vendor.id,
                        "origin": order.name,
                        "order_line": [(0, 0, {
                            "product_id": product.id,
                            "product_qty": 50,
                            "price_unit": product.standard_price,
                            "name": product.name,
                        })]
                    })
        return res