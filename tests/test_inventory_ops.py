from datetime import timedelta
from unittest import SkipTest
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import SmartInventoryCase


@tagged("post_install", "-at_install")
class TestDemandPlanning(SmartInventoryCase):
    """Seasons and promotions, new products forecast like a similar product."""

    def test_event_factor_in_baseline(self):
        engine = self.env["smart.inventory.forecast.engine"]
        end = fields.Date.today()
        flat = {end - timedelta(days=i): 10.0 for i in range(1, 91)}
        plain = engine._baseline(flat, 7, end=end)
        boost = {end + timedelta(days=d): 1.5 for d in range(7)}
        boosted = engine._baseline(flat, 7, end=end, day_factors=boost)
        self.assertAlmostEqual(boosted["forecast"], plain["forecast"] * 1.5, places=4)
        self.assertAlmostEqual(boosted["daily"], plain["daily"], places=4)

        # A past event doubled sales on its days: dividing by its factor keeps the baseline flat.
        spiky = dict(flat)
        past_event = {}
        for i in range(10, 14):
            day = end - timedelta(days=i)
            spiky[day] = 20.0
            past_event[day] = 2.0
        self.assertGreater(engine._baseline(spiky, 7, end=end)["daily"], plain["daily"])
        self.assertAlmostEqual(engine._baseline(spiky, 7, end=end, day_factors=past_event)["daily"],
                               plain["daily"], places=4)

    def test_event_scope(self):
        Event = self.env["smart.inventory.demand.event"]
        today = fields.Date.today()
        rice = self.make_product("SI Event Rice")
        other_categ = self.env["product.category"].create({"name": "SI Other"})
        oil = self.make_product("SI Event Oil", categ_id=other_categ.id)
        event = Event.create({"name": "Eid", "date_from": today, "date_to": today + timedelta(days=2),
                              "factor": 1.5, "categ_ids": [(6, 0, self.category.ids)]})
        self.assertEqual(len(Event._day_factors(rice, today, today + timedelta(days=6))), 3)
        self.assertFalse(Event._day_factors(oil, today, today + timedelta(days=6)))
        event.write({"categ_ids": [(5,)], "product_tmpl_ids": [(6, 0, oil.ids)]})
        self.assertEqual(Event._day_factors(oil, today, today)[today], 1.5)
        self.assertFalse(Event._day_factors(rice, today, today))
        with self.assertRaises(Exception):
            Event.create({"name": "Bad", "date_from": today, "date_to": today - timedelta(days=1)})

    def test_forecast_uses_event(self):
        normal = self.make_product("SI Normal")
        promo = self.make_product("SI Promo")
        today = self.env["smart.inventory.forecast.engine"]._today()
        self.env["smart.inventory.demand.event"].create({
            "name": "Promotion", "date_from": today, "date_to": today + timedelta(days=30),
            "factor": 2.0, "product_tmpl_ids": [(6, 0, promo.ids)]})
        with self.patch_daily_sales(4):
            (normal | promo)._si_update_forecasts()
        self.assertGreater(normal.predicted_demand, 0)
        self.assertAlmostEqual(promo.predicted_demand, normal.predicted_demand * 2, places=2)

    def test_new_product_forecast_like_similar(self):
        old = self.make_product("SI Old Seller")
        for days_ago in range(1, 21):
            self.past_sale(old, 5, days_ago=days_ago)
        new = self.make_product("SI New Launch", si_similar_product_id=old.id)
        alone = self.make_product("SI New Alone")
        (old | new | alone)._si_update_forecasts()
        self.assertGreater(old.avg_daily_demand, 0)
        self.assertAlmostEqual(new.avg_daily_demand, old.avg_daily_demand, places=4)
        self.assertEqual(alone.avg_daily_demand, 0)
        level = self.env["smart.inventory.stock.level"].search([
            ("product_id", "=", new.product_variant_id.id), ("warehouse_id", "=", self.warehouse.id)])
        self.assertGreater(level.daily_demand, 0)


@tagged("post_install", "-at_install")
class TestPurchasingControl(SmartInventoryCase):
    """Suggestions inbox, weekly budget, restock reason."""

    def tearDown(self):
        self.set_param("restock_review", "False")
        self.set_param("weekly_budget", "0")
        super().tearDown()

    def suggestions(self, templates):
        return self.env["smart.inventory.suggestion"].search(
            [("product_id", "in", templates.product_variant_ids.ids)])

    def test_review_approve_edit(self):
        self.set_param("restock_review", "True")
        product = self.make_product("SI Review")
        self.set_stock(product, 2)
        product.action_check_and_restock()
        self.assertFalse(self.smart_pos(product))
        suggestion = self.suggestions(product)
        self.assertEqual(len(suggestion), 1)
        self.assertEqual(suggestion.state, "new")
        self.assertEqual(suggestion.suggested_qty, 50)
        self.assertIn("restock level 10", suggestion.reason)
        self.assertEqual(suggestion.amount, 50 * 8.0)
        # rerun: the open suggestion counts as incoming, nothing is added
        product.action_check_and_restock()
        self.assertEqual(self.suggestions(product).quantity, 50)

        suggestion.quantity = 70
        suggestion.action_approve()
        self.assertEqual(suggestion.state, "approved")
        po = self.smart_pos(product)
        self.assertEqual(suggestion.purchase_order_id, po)
        self.assertEqual(po.order_line.product_qty, 70)
        # the PO now covers the need
        product.action_check_and_restock()
        self.assertEqual(len(self.suggestions(product)), 1)

    def test_reject_snoozes(self):
        self.set_param("restock_review", "True")
        product = self.make_product("SI Reject")
        self.set_stock(product, 0)
        product.action_check_and_restock()
        suggestion = self.suggestions(product)
        action = suggestion.action_reject()
        wizard = self.env[action["res_model"]].with_context(action["context"]).create({"reason": "Season over"})
        wizard.action_reject()
        self.assertEqual(suggestion.state, "rejected")
        self.assertEqual(suggestion.reject_reason, "Season over")
        product.action_check_and_restock()
        self.assertEqual(len(self.suggestions(product)), 1)
        self.assertFalse(self.smart_pos(product))

    def test_weekly_budget(self):
        self.set_param("weekly_budget", "1000000")
        spent = 1000000 - self.env["purchase.order"]._si_budget_left(self.company)
        self.set_param("weekly_budget", str(spent + 500))
        first = self.make_product("SI Budget First")
        second = self.make_product("SI Budget Second")
        self.set_stock(first, 0)
        self.set_stock(second, 0)
        (first | second).action_check_and_restock()
        # 50 units x 8 = 400 fits; the second 400 does not
        self.assertTrue(self.smart_pos(first))
        self.assertFalse(self.smart_pos(second))
        over = self.suggestions(second)
        self.assertTrue(over.over_budget)
        self.assertIn("budget", over.reason)
        self.assertAlmostEqual(self.env["purchase.order"]._si_budget_left(self.company), 100.0)
        with self.assertRaises(UserError):
            over.action_approve()
        self.set_param("weekly_budget", str(spent + 1000))
        over.action_approve()
        self.assertTrue(self.smart_pos(second))

    def test_no_review_no_budget_unchanged(self):
        product = self.make_product("SI Direct")
        self.set_stock(product, 1)
        product.action_check_and_restock()
        self.assertTrue(self.smart_pos(product))
        self.assertFalse(self.suggestions(product))


@tagged("post_install", "-at_install")
class TestStockMoney(SmartInventoryCase):
    """Overstock, lost sales, dead stock actions, dashboard money view."""

    def test_overstock(self):
        self.set_param("overstock_days", "120")
        product = self.make_product("SI Overstock")
        normal = self.make_product("SI Right Size")
        self.set_stock(product, 500)
        self.set_stock(normal, 50)
        (product | normal).sudo().write({"avg_daily_demand": 1.0})
        self.assertTrue(product.is_overstock)
        self.assertAlmostEqual(product.si_excess_value, (500 - 120) * 10.0)
        self.assertFalse(normal.is_overstock)
        found = self.env["product.template"].search([("is_overstock", "=", True), ("categ_id", "=", self.category.id)])
        self.assertEqual(found, product)

    def test_stockout_log_and_dashboard(self):
        product = self.make_product("SI Stockout", list_price=20.0)
        self.set_stock(product, 0)
        product.sudo().write({"avg_daily_demand": 2.0})
        Stockout = self.env["smart.inventory.stockout"]
        Stockout._cron_log_stockouts()
        Stockout._cron_log_stockouts()
        row = Stockout.search([("product_tmpl_id", "=", product.id)])
        self.assertEqual(len(row), 1)
        self.assertEqual(row.lost_value, 40.0)
        data = self.env["smart.inventory.dashboard"].get_dashboard_data(30, {"categ_id": self.category.id})
        self.assertEqual(data["savings"]["lostSales"], 40.0)
        for key in ("tiedUp", "savedByTransfers", "budgetLeft", "forecastError"):
            self.assertIn(key, data["savings"])
        self.assertIn("count", data["suggestions"])
        self.assertIn("overstockCount", data)
        self.assertIn("cycleCountsDue", data)

    def test_dead_stock_actions(self):
        product = self.make_product("SI Dead Clear")
        self.set_stock(product, 7)
        Wizard = self.env["smart.inventory.dead.stock.wizard"].with_context(
            active_model="product.template", active_ids=product.ids)
        pricelist = self.env["product.pricelist"].create({"name": "SI Clearance"})
        Wizard.create({"action": "discount", "pricelist_id": pricelist.id, "discount": 30}).action_apply()
        item = pricelist.item_ids.filtered(lambda i: i.product_tmpl_id == product)
        self.assertEqual(item.percent_price, 30)

        result = Wizard.create({"action": "return", "warehouse_id": self.warehouse.id,
                                "vendor_id": self.vendor.id}).action_apply()
        picking = self.env["stock.picking"].browse(result["res_id"])
        self.assertEqual(picking.partner_id, self.vendor)
        self.assertEqual(picking.move_ids.product_uom_qty, 7)
        self.assertEqual(picking.location_dest_id, self.vendor.property_stock_supplier)

        with self.assertRaises(UserError):
            Wizard.create({"action": "transfer", "warehouse_id": self.warehouse.id,
                           "dest_warehouse_id": self.warehouse.id}).action_apply()


@tagged("post_install", "-at_install")
class TestCounting(SmartInventoryCase):
    """Adjustment reasons, ABC cycle counts, FEFO."""

    def tearDown(self):
        self.set_param("require_adjust_reason", "False")
        self.set_param("cycle_count", "False")
        self.set_param("fefo", "False")
        super().tearDown()

    def count(self, template, qty, reason=None):
        quant = self.env["stock.quant"].with_context(inventory_mode=True).search([
            ("product_id", "=", template.product_variant_id.id), ("location_id", "=", self.stock_location.id)])
        quant.write({"inventory_quantity": qty, "si_reason_id": reason.id if reason else False})
        quant.action_apply_inventory()
        return quant

    def test_adjustment_reason(self):
        product = self.make_product("SI Count")
        self.set_stock(product, 10)
        damaged = self.env.ref("smart_inventory.reason_damaged")
        self.count(product, 7, damaged)
        move = self.env["stock.move"].search([("product_id", "=", product.product_variant_id.id),
                                              ("is_inventory", "=", True)], order="id desc", limit=1)
        self.assertEqual(move.si_reason_id, damaged)
        self.assertEqual(move.si_adjust_qty, -3)
        self.assertEqual(move.si_adjust_value, -30.0)
        quant = self.env["stock.quant"].search([("product_id", "=", product.product_variant_id.id),
                                                ("location_id", "=", self.stock_location.id)])
        self.assertFalse(quant.si_reason_id)

        self.set_param("require_adjust_reason", "True")
        with self.assertRaises(UserError):
            self.count(product, 5)
        self.count(product, 5, self.env.ref("smart_inventory.reason_theft"))
        self.assertEqual(product.qty_available, 5)

    def test_cycle_count_by_abc(self):
        product_a = self.make_product("SI Count A")
        product_c = self.make_product("SI Count C")
        self.set_stock(product_a, 5)
        self.set_stock(product_c, 5)
        (product_a | product_c).sudo().write({"abc_class": "a"})
        product_c.sudo().write({"abc_class": "c"})
        quants = self.env["stock.quant"].search([
            ("product_id", "in", (product_a | product_c).product_variant_ids.ids),
            ("location_id", "=", self.stock_location.id)])
        quants.write({"inventory_date": fields.Date.today() + timedelta(days=365)})
        Quant = self.env["stock.quant"]
        Quant._cron_schedule_cycle_counts()
        self.assertEqual(quants.mapped("inventory_date"), [fields.Date.today() + timedelta(days=365)] * 2)
        self.set_param("cycle_count", "True")
        Quant._cron_schedule_cycle_counts()
        quant_a = quants.filtered(lambda q: q.product_id == product_a.product_variant_id)
        quant_c = quants - quant_a
        self.assertEqual(quant_a.inventory_date, fields.Date.today() + timedelta(days=30))
        self.assertEqual(quant_c.inventory_date, fields.Date.today() + timedelta(days=180))
        action = Quant.action_si_cycle_count()
        self.assertEqual(action["context"].get("search_default_to_count"), 1)

    def test_fefo(self):
        if "use_expiration_date" not in self.env["product.template"]._fields:
            raise SkipTest("product_expiry is not installed")
        categ = self.env["product.category"].create({"name": "SI Perishable"})
        self.make_product("SI Milk", categ_id=categ.id, use_expiration_date=True)
        self.env["product.template"]._si_apply_fefo()
        self.assertEqual(categ.removal_strategy_id, self.env.ref("product_expiry.removal_fefo"))


@tagged("post_install", "-at_install")
class TestVendorScoreAndAlerts(SmartInventoryCase):

    def tearDown(self):
        self.set_param("telegram_token", "")
        self.set_param("telegram_chat_id", "")
        self.set_param("alert_webhook_url", "")
        super().tearDown()

    def test_vendor_score(self):
        vendor = self.env["res.partner"].create({"name": "SI Late Vendor", "supplier_rank": 1})
        product = self.make_product("SI Scored", vendor=vendor)
        po = self.env["purchase.order"].create({
            "partner_id": vendor.id,
            "order_line": [(0, 0, {"product_id": product.product_variant_id.id, "product_qty": 10,
                                   "price_unit": 8.0,
                                   "date_planned": fields.Datetime.now() - timedelta(days=3)})],
        })
        po.button_confirm()
        picking = po.picking_ids
        picking.scheduled_date = fields.Datetime.now() - timedelta(days=3)
        picking.move_ids.quantity = 8
        picking.move_ids.picked = True
        picking.with_context(skip_backorder=True, picking_ids_not_to_backorder=picking.ids).button_validate()
        self.assertEqual(picking.state, "done")
        self.assertEqual(po.order_line.qty_received, 8)
        Score = self.env["smart.inventory.vendor.score"]
        Score._refresh_company(self.company)
        score = Score.search([("partner_id", "=", vendor.id)])
        self.assertEqual(score.order_count, 1)
        self.assertEqual(score.fill_rate, 80.0)
        self.assertEqual(score.on_time_rate, 0.0)
        self.assertEqual(score.avg_delay_days, 3.0)
        self.assertEqual(score.score, 52.0)

    def test_high_risk_alert(self):
        product = self.make_product("SI Alert Me")
        self.set_stock(product, 1)
        target = "odoo.addons.smart_inventory.models.alert.requests.post"
        with patch(target) as post, self.patch_daily_sales(5):
            product._si_update_forecasts()
        post.assert_not_called()

        product.sudo().write({"risk_level": "low"})
        self.set_param("telegram_token", "TOKEN")
        self.set_param("telegram_chat_id", "42")
        self.set_param("alert_webhook_url", "https://gateway.example/hook")
        with patch(target) as post, self.patch_daily_sales(5):
            product._si_update_forecasts()
            self.assertEqual(post.call_count, 2)
            telegram_call = post.call_args_list[0]
            self.assertIn("botTOKEN/sendMessage", telegram_call.args[0])
            self.assertEqual(telegram_call.kwargs["json"]["chat_id"], "42")
            self.assertIn("SI Alert Me", telegram_call.kwargs["json"]["text"])
            # already high: no repeated alert
            product._si_update_forecasts()
            self.assertEqual(post.call_count, 2)

    def test_alert_failure_is_logged(self):
        import requests
        self.set_param("alert_webhook_url", "https://gateway.example/hook")
        with patch("odoo.addons.smart_inventory.models.alert.requests.post",
                   side_effect=requests.ConnectionError("down")), \
                self.assertLogs("odoo.addons.smart_inventory.models.alert", level="WARNING"):
            self.assertFalse(self.env["smart.inventory.alert"]._send("hello"))
