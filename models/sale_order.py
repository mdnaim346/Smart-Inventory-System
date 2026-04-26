from odoo import models, fields, api
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

class SaleOrder(models.Model):
    _inherit = "sale.order"

    def action_confirm(self):
        """ Override confirm to:
        1. Prevent selling if stock is insufficient.
        2. Create RFQs for low stock.
        3. Auto-invoice.
        4. Auto-validate delivery (to update On Hand quantity).
        """
        self._validate_stock_availability()
        res = super().action_confirm()
        self._check_low_stock_and_create_po()
        self._create_auto_invoice()
        self._validate_auto_delivery()
        return res

    def _validate_auto_delivery(self):
        """ Automatically validate the delivery order to update On Hand stock immediately. """
        for order in self:
            for picking in order.picking_ids:
                if picking.state not in ('done', 'cancel'):
                    _logger.info("Auto-validating delivery %s for order %s", picking.name, order.name)
                    # Assign stock and set quantities
                    picking.action_assign()
                    for move in picking.move_ids_without_package:
                        move.quantity = move.product_uom_qty
                    # Validate the picking (passing context to skip backorder/etc wizards if possible)
                    picking.with_context(skip_backorder=True, picking_label_types=False).button_validate()

    def _create_auto_invoice(self):
        """ Automatically create and post invoice on SO confirmation. """
        for order in self:
            try:
                if order.invoice_status == 'to invoice':
                    invoices = order._create_invoices()
                    invoices.action_post()
            except Exception as e:
                _logger.warning("Auto-invoice failed for %s: %s", order.name, str(e))

    def write(self, vals):
        """ Hook into write to catch custom status changes from dashboards. """
        if vals.get('status') == 'confirmed' or vals.get('state') == 'sale':
            self._validate_stock_availability()
            
        res = super().write(vals)
        # Check standard Odoo state 'sale' and any custom 'status' field set to 'confirmed'
        if vals.get('status') == 'confirmed' or vals.get('state') == 'sale':
            self._check_low_stock_and_create_po()
            self._create_auto_invoice()
            self._validate_auto_delivery()
        return res

    def _validate_stock_availability(self):
        """ Cross-checks order lines against available stock. """
        for order in self:
            for line in order.order_line:
                # Odoo 17 specific check for storable products
                is_storable = line.product_id.type == 'product'
                if is_storable and line.product_uom_qty > line.product_id.qty_available:
                    raise UserError(
                        f"OUT OF STOCK: Order for '{line.product_id.name}' blocked.\n"
                        f"Requested: {line.product_uom_qty}\n"
                        f"Available: {line.product_id.qty_available}\n"
                        f"Please restock before confirming."
                    )

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