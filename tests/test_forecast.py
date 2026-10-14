from datetime import date, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged

from .common import SmartInventoryCase

AI_REPLY = '{"prediction": 42, "risk": "high", "insight": "Promotion next week."}'


@tagged("post_install", "-at_install")
class TestForecastEngine(SmartInventoryCase):
    """E1.3 baseline, E2.1 safety stock, E1.4 MAPE, E3.4 classes (pure math)."""

    def setUp(self):
        super().setUp()
        self.engine = self.env["smart.inventory.forecast.engine"]
        self.end = date(2026, 10, 12)  # a Monday

    def test_flat_demand(self):
        sales = {self.end - timedelta(days=i): 2.0 for i in range(1, 91)}
        stats = self.engine._baseline(sales, 7, end=self.end)
        self.assertAlmostEqual(stats["daily"], 2.0)
        self.assertAlmostEqual(stats["forecast"], 14.0)
        self.assertAlmostEqual(stats["sigma"], 0.0)

    def test_no_history(self):
        stats = self.engine._baseline({}, 7, end=self.end)
        self.assertEqual((stats["daily"], stats["forecast"], stats["sigma"]), (0.0, 0.0, 0.0))

    def test_recent_sales_weigh_more(self):
        sales = {self.end - timedelta(days=i): 7.0 for i in range(1, 8)}
        stats = self.engine._baseline(sales, 7, end=self.end)
        expected = 0.5 * 7 + 0.3 * (49 / 30) + 0.2 * (49 / 90)
        self.assertAlmostEqual(stats["daily"], expected)

    def test_day_of_week_factor(self):
        # Sells 7 units every Saturday only: forecast for the next 7 days still totals ~7/day-avg*7
        sales = {self.end - timedelta(days=i): 7.0 for i in range(1, 91)
                 if (self.end - timedelta(days=i)).weekday() == 5}
        stats = self.engine._baseline(sales, 1, end=self.end)  # tomorrow is Tuesday
        self.assertAlmostEqual(stats["forecast"], 0.0)
        saturday = self.engine._baseline(sales, 6, end=self.end)  # Tue..Sun includes Saturday
        self.assertGreater(saturday["forecast"], 0.0)

    def test_reorder_point_and_safety_stock(self):
        rop, safety = self.engine._reorder_point(10.0, 0.0, 4, 95.0)
        self.assertAlmostEqual(rop, 40.0)
        self.assertAlmostEqual(safety, 0.0)
        rop, safety = self.engine._reorder_point(10.0, 2.0, 4, 95.0)
        self.assertAlmostEqual(safety, 1.6448536 * 2.0 * 2.0, places=4)
        self.assertAlmostEqual(rop, 40.0 + safety, places=4)

    def test_mape(self):
        self.assertAlmostEqual(self.engine._mape([(110, 100), (90, 100), (5, 0)]), 10.0)
        self.assertEqual(self.engine._mape([]), 0.0)

    def test_abc_xyz(self):
        self.assertEqual(self.engine._abc({1: 80, 2: 15, 3: 5, 4: 0}), {1: "a", 2: "b", 3: "c", 4: "c"})
        self.assertEqual(self.engine._xyz([5] * 13), "x")
        self.assertEqual(self.engine._xyz([0] * 12 + [10]), "z")
        self.assertEqual(self.engine._xyz([]), "z")


@tagged("post_install", "-at_install")
class TestAiParsing(SmartInventoryCase):
    """E1.2: validate the AI JSON reply before using it."""

    def parse(self, text):
        return self.env["product.template"]._parse_ai_response(text)

    def test_valid(self):
        self.assertEqual(self.parse(AI_REPLY), {"prediction": 42.0, "risk": "high", "insight": "Promotion next week."})
        fenced = '```json\n{"prediction": 3.5, "risk": "LOW", "insight": ""}\n```'
        self.assertEqual(self.parse(fenced), {"prediction": 3.5, "risk": "low", "insight": ""})

    def test_malformed(self):
        for text in ("PREDICTION:15, RISK:high", "{not json}", "", None, 12):
            self.assertIsNone(self.parse(text), text)

    def test_out_of_range(self):
        for text in ('{"prediction": -1, "risk": "low"}', '{"prediction": "10", "risk": "low"}',
                     '{"prediction": true, "risk": "low"}', '{"prediction": 1, "risk": "extreme"}',
                     '{"prediction": NaN, "risk": "low"}'):
            self.assertIsNone(self.parse(text), text)


@tagged("post_install", "-at_install")
class TestForecastRun(SmartInventoryCase):
    """E1.3/E1.4/E1.5 on real records."""

    def test_baseline_forecast_written(self):
        product = self.make_product("SI Forecast")
        self.set_stock(product, 5)
        with self.patch_daily_sales(2):
            product.action_generate_ai_insights()
        self.assertAlmostEqual(product.predicted_demand, 14.0)
        self.assertAlmostEqual(product.avg_daily_demand, 2.0)
        self.assertEqual(product.forecast_source, "baseline")
        self.assertEqual(product.risk_level, "high")  # 5 usable < 14 demand
        self.assertTrue(product.ai_insight)
        self.assertEqual(len(product.forecast_ids), 1)
        self.assertAlmostEqual(product.forecast_ids.predicted, 14.0)

    def test_sales_history_uses_order_date(self):
        product = self.make_product("SI History")
        self.past_sale(product, 30, days_ago=3)
        self.past_sale(product, 1000, days_ago=200)  # outside the 90-day window
        product.action_generate_ai_insights()
        expected_daily = 0.5 * 30 / 7 + 0.3 * 30 / 30 + 0.2 * 30 / 90
        self.assertAlmostEqual(product.avg_daily_demand, expected_daily, places=4)

    def test_actuals_and_mape(self):
        product = self.make_product("SI Accuracy")
        forecast = self.env["smart.inventory.forecast"].create({
            "product_tmpl_id": product.id,
            "product_id": product.product_variant_id.id,
            "date": fields.Date.today() - timedelta(days=10),
            "horizon": 7,
            "predicted": 12.0,
        })
        self.past_sale(product, 10, days_ago=8)
        self.past_sale(product, 99, days_ago=1)  # after the window
        self.env["smart.inventory.forecast"]._cron_fill_actuals()
        self.assertTrue(forecast.is_closed)
        self.assertAlmostEqual(forecast.actual, 10.0)
        self.assertAlmostEqual(forecast.abs_pct_error, 20.0)
        self.assertAlmostEqual(product.forecast_mape, 20.0)

    def test_ai_used_when_enabled_and_within_budget(self):
        self.set_param("ai_enabled", "True")
        self.set_param("ai_daily_limit", "2")
        self.env["ir.config_parameter"].sudo().set_param("smart_inventory.ai_calls", "")
        products = self.make_product("SI AI 1") | self.make_product("SI AI 2") | self.make_product("SI AI 3")
        cls = type(self.env["product.template"])
        with patch.object(cls, "_ask_ai", autospec=True, return_value=AI_REPLY) as ask:
            products.action_generate_ai_insights()
        self.assertEqual(ask.call_count, 2)
        self.assertEqual(products.mapped("forecast_source").count("ai"), 2)
        ai_product = products.filtered(lambda p: p.forecast_source == "ai")[0]
        self.assertEqual(ai_product.predicted_demand, 42.0)
        self.assertEqual(ai_product.risk_level, "high")

    def test_invalid_ai_reply_falls_back(self):
        self.set_param("ai_enabled", "True")
        self.set_param("ai_daily_limit", "100")
        product = self.make_product("SI AI Bad")
        cls = type(self.env["product.template"])
        with patch.object(cls, "_ask_ai", autospec=True, return_value="garbage"):
            product.action_generate_ai_insights()
        self.assertEqual(product.forecast_source, "baseline")

    def test_ai_error_does_not_break_run(self):
        self.set_param("ai_enabled", "True")
        product = self.make_product("SI AI Timeout")
        cls = type(self.env["product.template"])
        with patch.object(cls, "_ask_ai", autospec=True, side_effect=TimeoutError):
            product.action_generate_ai_insights()
        self.assertEqual(product.forecast_source, "baseline")

    def test_only_changed_products_refresh(self):
        fresh, changed, never = (self.make_product(n) for n in ("SI Fresh", "SI Changed", "SI Never"))
        (fresh | changed).action_generate_ai_insights()
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE product_template SET last_ai_update = now() at time zone 'utc' + interval '1 minute' "
            "WHERE id = %s", [fresh.id])
        self.env.cr.execute(
            "UPDATE product_template SET last_ai_update = now() at time zone 'utc' - interval '1 hour' "
            "WHERE id = %s", [changed.id])
        (fresh | changed).invalidate_recordset()
        self.past_sale(changed, 1, days_ago=0)
        todo = (fresh | changed | never)._si_needs_refresh(7)
        self.assertEqual(todo, changed | never)

    def test_cron_batches(self):
        self.set_param("ai_batch_size", "1")
        products = self.make_product("SI Batch 1") | self.make_product("SI Batch 2")
        self.env["product.template"]._cron_generate_insights()
        self.assertTrue(all(products.mapped("last_ai_update")))
