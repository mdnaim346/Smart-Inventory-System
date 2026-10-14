import logging
from collections import defaultdict

from odoo import models, _
from odoo.exceptions import UserError
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def action_confirm(self):
        """Confirm, then run the Smart Inventory automation once.
        Each step can be switched off per company in Settings (E5.2):
        1. block out-of-stock sales, 2. replenish low stock,
        3. validate the delivery, 4. post the invoice.
        """
        self.filtered(lambda o: o.company_id.si_block_out_of_stock)._validate_stock_availability()
        res = super().action_confirm()
        self._check_low_stock_and_create_po()
        self.filtered(lambda o: o.company_id.si_auto_deliver)._validate_auto_delivery()
        self.filtered(lambda o: o.company_id.si_auto_invoice)._create_auto_invoice()
        return res

    def _validate_stock_availability(self):
        """Block confirmation when a storable product lacks usable stock in the order's warehouse."""
        for order in self:
            needed = defaultdict(float)
            for line in order.order_line.filtered(lambda line: line.product_id.is_storable):
                needed[line.product_id] += line.product_uom_id._compute_quantity(
                    line.product_uom_qty, line.product_id.uom_id)
            for product, qty in needed.items():
                product_wh = product.with_context(warehouse_id=order.warehouse_id.id)
                expired = product._si_expired_qty(order.warehouse_id).get(product.id, 0.0)
                available = product_wh.qty_available - expired
                if float_compare(qty, available, precision_rounding=product.uom_id.rounding) > 0:
                    raise UserError(_(
                        "OUT OF STOCK: Order for '%(product)s' blocked.\n"
                        "Requested: %(requested)s\nAvailable: %(available)s\n"
                        "Please restock before confirming.",
                        product=product.display_name, requested=qty, available=available))

    def _check_low_stock_and_create_po(self):
        """Replenish low products of the confirmed orders (same path as the cron)."""
        for order in self:
            products = order.order_line.product_id.filtered("is_storable")
            if products:
                # Salespeople need no Purchase or Smart Inventory rights to trigger a restock.
                products.sudo().with_company(order.company_id)._si_smart_restock(
                    warehouses=order.warehouse_id.sudo())

    def _validate_auto_delivery(self):
        """Validate deliveries with the reserved quantities. Unreserved quantities
        become a backorder or are dropped, depending on the company setting."""
        for order in self:
            backorder = order.company_id.si_create_backorder
            for picking in order.picking_ids.filtered(lambda p: p.state not in ("done", "cancel")):
                picking.action_assign()
                moves = picking.move_ids.filtered(lambda m: m.state not in ("done", "cancel"))
                if not any(m.quantity for m in moves):
                    continue
                ctx = {"skip_backorder": True, "skip_sms": True, "skip_immediate": True}
                if not backorder:
                    ctx["picking_ids_not_to_backorder"] = picking.ids
                try:
                    with self.env.cr.savepoint():
                        picking.with_context(**ctx).button_validate()
                except UserError as e:
                    _logger.warning("Auto-delivery of %s for %s skipped: %s", picking.name, order.name, e)

    def _create_auto_invoice(self):
        """Create and post the invoice of confirmed orders."""
        for order in self:
            if order.invoice_status != "to invoice":
                continue
            try:
                with self.env.cr.savepoint():
                    order._create_invoices().action_post()
            except UserError as e:
                _logger.warning("Auto-invoice failed for %s: %s", order.name, e)
