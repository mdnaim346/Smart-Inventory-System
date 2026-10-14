from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import SmartInventoryCase


@tagged("post_install", "-at_install")
class TestStockStatus(SmartInventoryCase):
    """Issue #1: low-stock flag and shortage always follow the real quantity."""

    def test_flags_follow_stock_moves(self):
        product = self.make_product("SI Status")
        self.assertTrue(product.is_low_stock)
        self.assertEqual(product.forecast_shortage, 10)
        self.set_stock(product, 100)
        self.assertFalse(product.is_low_stock)
        self.assertEqual(product.forecast_shortage, 0)
        self.set_stock(product, 4)
        self.assertTrue(product.is_low_stock)
        self.assertEqual(product.forecast_shortage, 6)

    def test_search_uses_live_values(self):
        low = self.make_product("SI Search Low")
        ok = self.make_product("SI Search OK")
        self.set_stock(ok, 50)
        found = self.env["product.template"].search([("is_low_stock", "=", True), ("categ_id", "=", self.category.id)])
        self.assertIn(low, found)
        self.assertNotIn(ok, found)
        not_low = self.env["product.template"].search([("is_low_stock", "=", False), ("categ_id", "=", self.category.id)])
        self.assertIn(ok, not_low)
        short = self.env["product.template"].search([("forecast_shortage", ">", 5), ("categ_id", "=", self.category.id)])
        self.assertEqual(short, low)


@tagged("post_install", "-at_install")
class TestReplenishment(SmartInventoryCase):
    """E2.1 – E2.6"""

    def test_one_po_per_vendor_and_no_duplicates(self):
        p1, p2 = self.make_product("SI Cons 1"), self.make_product("SI Cons 2")
        p3 = self.make_product("SI Cons 3", vendor=self.vendor_b)
        products = p1 | p2 | p3
        products.action_check_and_restock()
        pos = self.smart_pos(products)
        self.assertEqual(len(pos), 2)
        po_a = pos.filtered(lambda po: po.partner_id == self.vendor)
        self.assertEqual(po_a.order_line.product_id, (p1 | p2).product_variant_ids)
        self.assertEqual(po_a.order_line.mapped("product_qty"), [50.0, 50.0])
        self.assertEqual(po_a.origin, "Smart Restock")
        products.action_check_and_restock()
        self.assertEqual(self.smart_pos(products), pos)
        self.assertEqual(po_a.order_line.mapped("product_qty"), [50.0, 50.0])

    def test_confirmed_po_counts_as_incoming(self):
        product = self.make_product("SI Incoming")
        product.action_check_and_restock()
        self.smart_pos(product).button_confirm()
        product.action_check_and_restock()
        self.assertEqual(len(self.smart_pos(product)), 1)

    def test_vendor_selection(self):
        product = self.make_product("SI Vendors", price=8.0)
        self.env["product.supplierinfo"].create({
            "partner_id": self.vendor_b.id, "product_tmpl_id": product.id, "price": 5.0})
        self.assertEqual(product._get_vendor(), self.vendor_b)
        expired = self.env["res.partner"].create({"name": "SI Expired", "supplier_rank": 1})
        self.env["product.supplierinfo"].create({
            "partner_id": expired.id, "product_tmpl_id": product.id, "price": 1.0,
            "date_end": fields.Date.today() - timedelta(days=1)})
        self.assertEqual(product._get_vendor(), self.vendor_b)
        product.preferred_vendor_id = self.vendor
        self.assertEqual(product._get_vendor(), self.vendor)
        product.action_check_and_restock()
        line = self.smart_pos(product).order_line
        self.assertEqual(line.order_id.partner_id, self.vendor)
        self.assertEqual(line.price_unit, 8.0)  # supplier price, not standard_price

    def test_moq_and_pack_size(self):
        product = self.make_product("SI MOQ")
        product.seller_ids.write({"min_qty": 60, "si_pack_multiple": 25})
        product.action_check_and_restock()
        self.assertEqual(self.smart_pos(product).order_line.product_qty, 75.0)

    def test_no_vendor_no_po(self):
        product = self.make_product("SI No Vendor", seller_ids=[])
        product.action_check_and_restock()
        self.assertFalse(self.smart_pos(product))

    def test_computed_reorder_point(self):
        product = self.make_product("SI Auto ROP", delay=5, reorder_mode="auto")
        self.set_stock(product, 20)
        with self.patch_daily_sales(2):
            product.action_check_and_restock()
        self.assertAlmostEqual(product.reorder_point, 10.0)  # 2/day * 5 days, no variability
        self.assertAlmostEqual(product.safety_stock, 0.0)
        # 20 on hand > ROP 10 but < 7-day demand 14? no: 20 >= 14 -> nothing to order
        self.assertFalse(self.smart_pos(product))
        self.set_stock(product, 8)
        with self.patch_daily_sales(2):
            product.action_check_and_restock()
        # target = 2/day * 14 cover days = 28; position 8 -> order 20
        self.assertEqual(self.smart_pos(product).order_line.product_qty, 20.0)

    def test_approval_threshold(self):
        self.set_param("po_approval_amount", "100")
        product = self.make_product("SI Approval")
        product.action_check_and_restock()
        po = self.smart_pos(product)
        self.assertEqual(po.si_approval_state, "pending")
        with self.assertRaises(UserError):
            po.button_confirm()
        po.action_si_approve()
        self.assertEqual(po.si_approval_state, "approved")
        po.button_confirm()
        self.assertEqual(po.state, "purchase")

    def test_reject_with_reason(self):
        self.set_param("po_approval_amount", "100")
        product = self.make_product("SI Reject")
        product.action_check_and_restock()
        po = self.smart_pos(product)
        action = po.action_si_reject()
        wizard = self.env[action["res_model"]].with_context(action["context"]).create({"reason": "Too much"})
        wizard.action_reject()
        self.assertEqual(po.si_approval_state, "rejected")
        self.assertEqual(po.si_reject_reason, "Too much")
        with self.assertRaises(UserError):
            po.button_confirm()

    def test_below_threshold_needs_no_approval(self):
        self.set_param("po_approval_amount", "100000")
        product = self.make_product("SI Small")
        product.action_check_and_restock()
        po = self.smart_pos(product)
        self.assertEqual(po.si_approval_state, "not_required")
        po.button_confirm()
        self.assertEqual(po.state, "purchase")

    def test_orderpoint_mode(self):
        self.set_param("replenish_mode", "orderpoint")
        product = self.make_product("SI Orderpoint")
        product.action_check_and_restock()
        self.assertFalse(self.smart_pos(product))
        orderpoint = self.env["stock.warehouse.orderpoint"].search(
            [("product_id", "=", product.product_variant_id.id), ("warehouse_id", "=", self.warehouse.id)])
        self.assertEqual(orderpoint.product_min_qty, 10.0)
        self.assertEqual(orderpoint.product_max_qty, 60.0)

    def test_auto_restock_off(self):
        product = self.make_product("SI Off", auto_restock=False)
        product.action_check_and_restock()
        self.assertFalse(self.smart_pos(product))

    def test_cron_restock(self):
        product = self.make_product("SI Cron")
        self.env["product.template"]._cron_auto_restock()
        self.assertTrue(self.smart_pos(product))


@tagged("post_install", "-at_install")
class TestMultiWarehouse(SmartInventoryCase):
    """E3.1 / E3.2"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse_2 = cls.env["stock.warehouse"].create({
            "name": "SI Second Warehouse", "code": "SIW2", "company_id": cls.company.id})

    def test_per_warehouse_forecast(self):
        product = self.make_product("SI WH Forecast")
        self.past_sale(product, 70, days_ago=2, warehouse=self.warehouse)
        product.action_generate_ai_insights()
        Level = self.env["smart.inventory.stock.level"]
        level_1 = Level.search([("product_id", "=", product.product_variant_id.id), ("warehouse_id", "=", self.warehouse.id)])
        level_2 = Level.search([("product_id", "=", product.product_variant_id.id), ("warehouse_id", "=", self.warehouse_2.id)])
        self.assertGreater(level_1.forecast, 0)
        self.assertEqual(level_2.forecast, 0)

    def test_transfer_before_buying(self):
        product = self.make_product("SI WH Transfer")
        self.set_stock(product, 200, self.warehouse_2.lot_stock_id)
        product.action_check_and_restock()
        suggestion = self.env["smart.inventory.transfer.suggestion"].search([("product_id", "=", product.product_variant_id.id)])
        self.assertEqual(suggestion.source_warehouse_id, self.warehouse_2)
        self.assertEqual(suggestion.dest_warehouse_id, self.warehouse)
        self.assertEqual(suggestion.quantity, 50.0)
        self.assertFalse(self.smart_pos(product))
        suggestion.action_create_transfer()
        self.assertEqual(suggestion.state, "done")
        self.assertEqual(suggestion.picking_id.location_id, self.warehouse_2.lot_stock_id)
        self.assertEqual(suggestion.picking_id.location_dest_id, self.stock_location)
        self.assertEqual(suggestion.picking_id.move_ids.product_uom_qty, 50.0)

    def test_buy_when_no_excess(self):
        product = self.make_product("SI WH Buy")
        product.action_check_and_restock()
        pos = self.smart_pos(product)
        self.assertEqual(pos.picking_type_id.warehouse_id, self.warehouse | self.warehouse_2)

    def test_warehouse_threshold_override(self):
        product = self.make_product("SI WH Override")
        self.set_stock(product, 30)
        self.set_stock(product, 30, self.warehouse_2.lot_stock_id)
        product.action_generate_ai_insights()
        level_2 = self.env["smart.inventory.stock.level"].search([
            ("product_id", "=", product.product_variant_id.id), ("warehouse_id", "=", self.warehouse_2.id)])
        level_2.min_qty_override = 40
        product.product_variant_ids._si_smart_restock()
        pos = self.smart_pos(product)
        self.assertEqual(pos.picking_type_id.warehouse_id, self.warehouse_2)
