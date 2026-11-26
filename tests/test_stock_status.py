from odoo.tests import tagged

from .common import SmartInventoryCase


@tagged("post_install", "-at_install")
class TestStockStatus(SmartInventoryCase):
    """Issue #1: low-stock flags and dashboard quantities follow stock changes at once."""

    def test_flags_follow_quant_updates(self):
        product = self.make_product("SI Live Status", low_stock_threshold=10)
        variant = product.product_variant_id
        Quant = self.env["stock.quant"]
        Quant._update_available_quantity(variant, self.stock_location, 5)
        self.env.invalidate_all()
        self.assertEqual(product.usable_qty, 5)
        self.assertTrue(product.is_low_stock)
        self.assertIn(product, self.env["product.template"].search([("is_low_stock", "=", True)]))

        Quant._update_available_quantity(variant, self.stock_location, 20)
        self.env.invalidate_all()
        self.assertEqual(product.usable_qty, 25)
        self.assertFalse(product.is_low_stock)
        self.assertNotIn(product, self.env["product.template"].search([("is_low_stock", "=", True)]))
        data = self.env["smart.inventory.dashboard"].get_dashboard_data(30, {"categ_id": self.category.id})
        self.assertNotIn(product.id, [p["id"] for p in data["lowStockProducts"]])

        Quant._update_available_quantity(variant, self.stock_location, -25)
        self.env.invalidate_all()
        self.assertTrue(product.is_low_stock)
        data = self.env["smart.inventory.dashboard"].get_dashboard_data(30, {"categ_id": self.category.id})
        row = next(p for p in data["lowStockProducts"] if p["id"] == product.id)
        self.assertEqual(row["qty_available"], 0)
