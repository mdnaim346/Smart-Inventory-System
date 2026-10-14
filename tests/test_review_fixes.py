from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from .common import SmartInventoryCase


@tagged("post_install", "-at_install")
class TestReviewFixes(SmartInventoryCase):
    """Regression tests for issues found in code review."""

    def test_salesperson_confirm_triggers_restock(self):
        salesman = new_test_user(self.env, "si_salesman", groups="base.group_user,sales_team.group_sale_salesman")
        product = self.make_product("SI Salesman")
        self.set_stock(product, 12)
        order = self.env["sale.order"].with_user(salesman).create({
            "partner_id": self.customer.id,
            "order_line": [(0, 0, {"product_id": product.product_variant_id.id, "product_uom_qty": 5})],
        })
        self.company.si_auto_invoice = False
        order.action_confirm()
        self.assertEqual(order.state, "sale")
        self.assertTrue(self.smart_pos(product))

    def test_increase_after_approval_needs_new_approval(self):
        self.set_param("po_approval_amount", "100")
        product = self.make_product("SI Bypass")
        product.action_check_and_restock()
        po = self.smart_pos(product)
        po.action_si_approve()
        po.order_line.product_qty = 500
        po.button_confirm()
        self.assertEqual(po.state, "draft")
        self.assertEqual(po.si_approval_state, "pending")
        with self.assertRaises(UserError):
            po.button_confirm()

    def test_small_po_raised_above_threshold_needs_approval(self):
        self.set_param("po_approval_amount", "1000")
        product = self.make_product("SI Grow")
        product.action_check_and_restock()
        po = self.smart_pos(product)
        self.assertEqual(po.si_approval_state, "not_required")
        po.order_line.product_qty = 1000
        po.button_confirm()
        self.assertEqual(po.state, "draft")
        self.assertEqual(po.si_approval_state, "pending")

    def test_existing_line_in_other_uom(self):
        dozen = self.env.ref("uom.product_uom_dozen")
        p1 = self.make_product("SI UoM")
        p1.action_check_and_restock()
        line = self.smart_pos(p1).order_line
        line.write({"product_uom_id": dozen.id, "product_qty": 1})
        self.env["purchase.order"]._si_add_restock_line(
            self.vendor, self.warehouse, p1.product_variant_id, 24.0)
        self.assertAlmostEqual(line.product_qty, 3.0)  # 1 dozen + 24 units = 3 dozens

    def test_context_cannot_skip_manager_check(self):
        user = new_test_user(self.env, "si_ctx_user", groups="base.group_user,smart_inventory.group_smart_inventory_user")
        product = self.make_product("SI Ctx").with_user(user)
        with self.assertRaises(AccessError):
            product.with_context(install_mode=True).write({"low_stock_threshold": 1})
        with self.assertRaises(AccessError):
            product.with_context(si_from_cron=True).action_generate_ai_insights()

    def test_full_catalog_restock_is_queued(self):
        product = self.make_product("SI Queued")
        self.env["product.template"].action_check_and_restock()
        self.assertFalse(self.smart_pos(product))

    def test_transfer_covers_only_part(self):
        warehouse_2 = self.env["stock.warehouse"].create({"name": "SI WH Part", "code": "SIWP"})
        product = self.make_product("SI Partial")
        self.set_stock(product, 30, warehouse_2.lot_stock_id)
        product.action_generate_ai_insights()
        Level = self.env["smart.inventory.stock.level"]
        variant = product.product_variant_id
        # excess in WH2 = 30 - 10 = 20 < 50 -> no transfer, buy everything
        self.assertEqual(Level._si_suggest_transfer(variant, self.warehouse, 50.0), 50.0)
        self.assertEqual(Level._si_suggest_transfer(variant, self.warehouse, 15.0), 0.0)
        # 15 already promised: a need of 40 still buys 25, and WH2 has only 5 excess left
        self.assertEqual(Level._si_suggest_transfer(variant, self.warehouse, 40.0), 25.0)
        level_2 = Level.search([("product_id", "=", variant.id), ("warehouse_id", "=", warehouse_2.id)])
        self.assertEqual(level_2.excess_qty, 5.0)

    def test_stale_warehouse_filter_ignored(self):
        data = self.env["smart.inventory.dashboard"].get_dashboard_data(30, {"warehouse_id": 999999})
        self.assertIn("kpis", data)
