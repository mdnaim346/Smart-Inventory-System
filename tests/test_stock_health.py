from datetime import timedelta
from unittest import SkipTest

from odoo import fields
from odoo.tests import tagged

from .common import SmartInventoryCase


@tagged("post_install", "-at_install")
class TestStockHealth(SmartInventoryCase):
    """E3.3 expiry, E3.4 ABC/XYZ, E3.5 dead stock"""

    def age_product(self, product, days):
        self.env.cr.execute("UPDATE product_template SET create_date = %s WHERE id = %s",
                            [fields.Datetime.now() - timedelta(days=days), product.id])
        product.invalidate_recordset(["create_date"])

    def test_dead_stock(self):
        self.set_param("dead_stock_days", "90")
        dead = self.make_product("SI Dead")
        self.set_stock(dead, 5)
        self.age_product(dead, 200)
        self.past_sale(dead, 1, days_ago=120)
        alive = self.make_product("SI Alive")
        self.set_stock(alive, 5)
        self.age_product(alive, 200)
        self.past_sale(alive, 1, days_ago=10)
        new = self.make_product("SI New")  # created today, never sold
        self.set_stock(new, 5)
        self.env["product.template"]._cron_update_stock_health()
        self.assertTrue(dead.is_dead_stock)
        self.assertFalse(alive.is_dead_stock)
        self.assertFalse(new.is_dead_stock)
        self.assertTrue(dead.last_sale_date)
        # dead stock is below its threshold but is never restocked
        (dead | alive).action_check_and_restock()
        self.assertFalse(self.smart_pos(dead))
        self.assertTrue(self.smart_pos(alive))
        rows = self.env["smart.inventory.dashboard"]._export_rows("dead", {"categ_id": self.category.id})[2]
        self.assertEqual([r[0] for r in rows], [dead.display_name])

    def test_abc_xyz_classification(self):
        a = self.make_product("SI A", list_price=100)
        b = self.make_product("SI B", list_price=100)
        c = self.make_product("SI C", list_price=100)
        for week in range(13):
            self.past_sale(a, 8, days_ago=week * 7 + 1)
        self.past_sale(b, 20, days_ago=3)
        self.past_sale(c, 1, days_ago=3)
        self.env["product.template"]._cron_classify_abc_xyz()
        # The category isolates nothing here: other demo products also count, so only check ordering
        self.assertEqual(a.xyz_class, "x")
        self.assertEqual(b.xyz_class, "z")
        order = "abc"
        self.assertLessEqual(order.index(a.abc_class), order.index(c.abc_class))
        self.assertLessEqual(order.index(b.abc_class), order.index(c.abc_class))

    def test_service_level_by_class(self):
        product = self.make_product("SI Class SL")
        self.set_param("service_level", "95")
        self.set_param("service_level_a", "99")
        self.assertEqual(product._si_service_level(), 95.0)
        product.abc_class = "a"
        self.assertEqual(product._si_service_level(), 99.0)

    def test_expired_lots_not_usable(self):
        if "expiration_date" not in self.env["stock.lot"]._fields:
            raise SkipTest("product_expiry is not installed")
        self.set_param("expiry_alert_days", "30")
        product = self.make_product("SI Expiry", tracking="lot", use_expiration_date=True)
        variant = product.product_variant_id
        Lot = self.env["stock.lot"]
        expired = Lot.create({"name": "SI-OLD", "product_id": variant.id,
                              "expiration_date": fields.Datetime.now() - timedelta(days=1)})
        soon = Lot.create({"name": "SI-SOON", "product_id": variant.id,
                           "expiration_date": fields.Datetime.now() + timedelta(days=10)})
        Quant = self.env["stock.quant"]
        Quant._update_available_quantity(variant, self.stock_location, 30, lot_id=expired)
        Quant._update_available_quantity(variant, self.stock_location, 5, lot_id=soon)
        product.invalidate_recordset()
        self.assertEqual(product.qty_available, 35)
        self.assertEqual(product.usable_qty, 5)
        self.assertTrue(product.is_low_stock)
        lots = self.env["smart.inventory.dashboard"]._expiring_lots(variant, self.env["stock.warehouse"])
        self.assertEqual([lot["lot"] for lot in lots], ["SI-OLD", "SI-SOON"])
        self.assertTrue(lots[0]["expired"])
