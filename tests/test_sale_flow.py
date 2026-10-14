from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import SmartInventoryCase


@tagged("post_install", "-at_install")
class TestSaleFlow(SmartInventoryCase):
    """E5.1 Odoo 19 storable checks, E5.2 configurable automation"""

    def order(self, product, qty):
        return self.env["sale.order"].create({
            "partner_id": self.customer.id,
            "order_line": [(0, 0, {"product_id": product.product_variant_id.id, "product_uom_qty": qty})],
        })

    def test_out_of_stock_blocked(self):
        product = self.make_product("SI Sale Block")
        order = self.order(product, 5)
        with self.assertRaisesRegex(UserError, "OUT OF STOCK"):
            order.action_confirm()
        self.assertEqual(order.state, "draft")

    def test_full_automation(self):
        product = self.make_product("SI Sale Auto")
        self.set_stock(product, 12)
        order = self.order(product, 5)
        order.action_confirm()
        self.assertEqual(order.state, "sale")
        self.assertEqual(order.picking_ids.state, "done")
        self.assertEqual(order.order_line.qty_delivered, 5)
        self.assertEqual(len(order.invoice_ids), 1)
        self.assertEqual(order.invoice_ids.state, "posted")
        self.assertEqual(product.qty_available, 7)
        # 7 left <= threshold 10 -> restock PO from the sale
        self.assertEqual(self.smart_pos(product).order_line.product_qty, 50.0)

    def test_quantities_summed_per_product(self):
        product = self.make_product("SI Sale Sum")
        self.set_stock(product, 8)
        order = self.order(product, 5)
        order.order_line = [(0, 0, {"product_id": product.product_variant_id.id, "product_uom_qty": 5})]
        with self.assertRaises(UserError):
            order.action_confirm()

    def test_toggles_off(self):
        self.company.write({"si_block_out_of_stock": False, "si_auto_deliver": False, "si_auto_invoice": False})
        product = self.make_product("SI Sale Manual")
        order = self.order(product, 5)
        order.action_confirm()
        self.assertEqual(order.state, "sale")
        self.assertNotEqual(order.picking_ids.state, "done")
        self.assertFalse(order.invoice_ids)

    def test_partial_delivery_with_backorder(self):
        self.company.write({"si_block_out_of_stock": False, "si_create_backorder": True})
        product = self.make_product("SI Sale Backorder")
        self.set_stock(product, 3)
        order = self.order(product, 5)
        order.action_confirm()
        done = order.picking_ids.filtered(lambda p: p.state == "done")
        self.assertEqual(done.move_ids.quantity, 3)
        self.assertEqual(len(order.picking_ids), 2)
        self.assertEqual(product.qty_available, 0)

    def test_partial_delivery_without_backorder(self):
        self.company.write({"si_block_out_of_stock": False, "si_create_backorder": False})
        product = self.make_product("SI Sale No Backorder")
        self.set_stock(product, 3)
        order = self.order(product, 5)
        order.action_confirm()
        self.assertEqual(len(order.picking_ids), 1)
        self.assertEqual(order.picking_ids.state, "done")
        self.assertEqual(order.order_line.qty_delivered, 3)

    def test_nothing_reserved_leaves_delivery_open(self):
        self.company.write({"si_block_out_of_stock": False})
        product = self.make_product("SI Sale Zero")
        order = self.order(product, 5)
        order.action_confirm()
        self.assertNotEqual(order.picking_ids.state, "done")
        self.assertEqual(product.qty_available, 0)

    def test_automation_runs_once(self):
        product = self.make_product("SI Sale Once")
        self.set_stock(product, 100)
        order = self.order(product, 5)
        order.action_confirm()
        order.write({"state": "sale"})
        self.assertEqual(len(order.invoice_ids), 1)
        self.assertEqual(len(order.picking_ids), 1)
