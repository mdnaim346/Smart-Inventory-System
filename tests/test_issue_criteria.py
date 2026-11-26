import json
import logging
from unittest.mock import MagicMock, patch

from odoo.tests import tagged

from .common import SmartInventoryCase
from .test_ai_provider import REPLY, fake_response


@tagged("post_install", "-at_install")
class TestIssueCriteria(SmartInventoryCase):
    """Acceptance criteria of roadmap issues not covered elsewhere."""

    def tearDown(self):
        for key, value in (("replenish_mode", "po"), ("order_qty_mode", "cover"), ("ai_enabled", "False"),
                           ("ai_api_key", ""), ("ai_batch_size", "50")):
            self.set_param(key, value)
        super().tearDown()

    # E1.1 (#5): the API key never reaches logs or chatter
    def test_api_key_not_logged(self):
        secret = "sk-ant-SECRET-123456"
        self.set_param("ai_enabled", "True")
        self.set_param("ai_api_key", secret)
        self.env["ir.config_parameter"].sudo().set_param("smart_inventory.ai_calls", "")
        product = self.make_product("SI Key Safe")
        client = MagicMock()
        client.beta.messages.create.return_value = fake_response(json.dumps(REPLY))
        Template = type(self.env["product.template"])
        with patch.object(Template, "_si_ai_client", autospec=True, return_value=client), \
                self.assertLogs("odoo.addons.smart_inventory", level=logging.DEBUG) as logs:
            product.action_generate_ai_insights()
            client.beta.messages.create.side_effect = RuntimeError("401 invalid key")
            product.action_generate_ai_insights()
        output = "\n".join(logs.output)
        self.assertIn("AI forecast for SI Key Safe", output)
        self.assertNotIn(secret, output)
        messages = self.env["mail.message"].search([("model", "=", "product.template"), ("res_id", "=", product.id)])
        self.assertFalse(any(secret in (m.body or "") for m in messages))

    # E1.5 (#9): a failing batch does not undo the batches before it
    def test_partial_failure_keeps_updated_products(self):
        self.set_param("ai_batch_size", "1")
        first, broken, last = (self.make_product(n) for n in ("SI Batch 1", "SI Batch 2", "SI Batch 3"))
        Template = type(self.env["product.template"])
        original = Template._si_update_forecasts

        def flaky(records):
            if broken in records:
                raise RuntimeError("provider down")
            return original(records)

        todo = first | broken | last
        with patch.object(Template, "_si_update_forecasts", flaky), \
                patch.object(Template, "_si_needs_refresh", lambda records, horizon: todo), \
                self.assertLogs("odoo.addons.smart_inventory.models.product", level="ERROR"):
            self.env["product.template"]._cron_generate_insights()
        self.assertTrue(first.last_ai_update)
        self.assertFalse(broken.last_ai_update)
        self.assertTrue(last.last_ai_update)

    # E2.2 (#12): optional economic order quantity
    def test_eoq_mode(self):
        product = self.make_product("SI EOQ", reorder_mode="auto")
        self.set_stock(product, 0)
        with self.patch_daily_sales(2):
            product._si_update_forecasts()
        variant = product.product_variant_id
        self.assertEqual(variant._si_eoq(2.0), 0.0)
        self.set_param("order_qty_mode", "eoq")
        self.set_param("order_cost", "500")
        self.set_param("holding_cost_pct", "20")
        # sqrt(2 x 730 x 500 / (10 x 20%)) = sqrt(365000) ~ 604
        self.assertAlmostEqual(variant._si_eoq(2.0), 604.15, places=1)
        with self.patch_daily_sales(2):
            product.action_check_and_restock()
        line = self.smart_pos(product).order_line
        self.assertGreaterEqual(line.product_qty, 604)

    # E2.6 (#16): orderpoint min is the computed reorder point, refreshed by each forecast run
    def test_orderpoint_min_is_reorder_point(self):
        self.set_param("replenish_mode", "orderpoint")
        product = self.make_product("SI OP Auto", reorder_mode="auto")
        self.set_stock(product, 500)
        with self.patch_daily_sales(3):
            product._si_update_forecasts()
        level = self.env["smart.inventory.stock.level"].search([
            ("product_id", "=", product.product_variant_id.id), ("warehouse_id", "=", self.warehouse.id)])
        orderpoint = self.env["stock.warehouse.orderpoint"].search([
            ("product_id", "=", product.product_variant_id.id), ("warehouse_id", "=", self.warehouse.id)])
        self.assertGreater(level.reorder_point, 0)
        self.assertAlmostEqual(orderpoint.product_min_qty, level.reorder_point, places=2)
        self.assertGreater(orderpoint.product_max_qty, orderpoint.product_min_qty)
        with self.patch_daily_sales(6):
            product._si_update_forecasts()
        self.assertAlmostEqual(orderpoint.product_min_qty, level.reorder_point, places=2)
        self.assertFalse(self.smart_pos(product))

    # E3.4 (#21): a class change changes safety stock at the next recompute
    def test_class_changes_safety_stock(self):
        self.set_param("service_level_a", "99")
        self.set_param("service_level_c", "80")
        product = self.make_product("SI Class Safety", reorder_mode="auto")
        engine_cls = type(self.env["smart.inventory.forecast.engine"])
        noisy = {}

        def fake(engine, products, window, warehouse=None, end=None):
            from datetime import timedelta
            from odoo import fields
            end = end or fields.Date.context_today(engine)
            series = {end - timedelta(days=i): float(2 + 4 * (i % 2)) for i in range(1, window + 1)}
            return {p.id: dict(series) for p in products}

        with patch.object(engine_cls, "_daily_sales", fake):
            product.sudo().abc_class = "c"
            product._si_update_forecasts()
            noisy["c"] = product.safety_stock
            product.sudo().abc_class = "a"
            product._si_update_forecasts()
            noisy["a"] = product.safety_stock
        self.assertGreater(noisy["c"], 0)
        self.assertGreater(noisy["a"], noisy["c"])

    # E4.1 (#24): days of cover = stock value / average daily cost of goods sold
    def test_days_of_cover_kpi(self):
        product = self.make_product("SI Cover KPI")
        self.set_stock(product, 100)
        order = self.env["sale.order"].create({
            "partner_id": self.customer.id,
            "order_line": [(0, 0, {"product_id": product.product_variant_id.id, "product_uom_qty": 10})],
        })
        order.action_confirm()
        self.assertEqual(order.order_line.qty_delivered, 10)
        kpis = self.env["smart.inventory.dashboard"].get_dashboard_data(30, {"categ_id": self.category.id})["kpis"]
        # stock value 90 x 10 = 900; cost of goods sold 10 x 10 = 100 over 30 days
        self.assertEqual(kpis["stockValue"], 900.0)
        self.assertEqual(kpis["daysOfCover"], 270.0)
        self.assertEqual(kpis["turnover"], round(100 / 900, 2))

    # E4.3 (#26): a filter updates every card, restock orders included
    def test_filters_apply_to_order_cards(self):
        mine = self.make_product("SI Filter Mine")
        self.set_stock(mine, 0)
        mine.action_check_and_restock()
        other_categ = self.env["product.category"].create({"name": "SI Filter Other"})
        dashboard = self.env["smart.inventory.dashboard"]
        data = dashboard.get_dashboard_data(30, {"categ_id": self.category.id})
        self.assertGreaterEqual(data["pendingOrders"], 1)
        self.assertTrue(data["recentActivities"])
        empty = dashboard.get_dashboard_data(30, {"categ_id": other_categ.id})
        self.assertEqual(empty["totalProducts"], 0)
        self.assertEqual(empty["pendingOrders"], 0)
        self.assertFalse(empty["recentActivities"])
        self.assertFalse(empty["pendingApprovals"])

    # E4.4 (#27): no e-mail on a day without high-risk products, even with pending POs
    def test_digest_only_with_risks(self):
        Digest = self.env["smart.inventory.digest"]
        self.env["product.template"].search([("risk_level", "=", "high")]).sudo().write({"risk_level": "low"})
        self.env.ref("base.user_admin").email = "admin@example.com"
        product = self.make_product("SI Digest Pending")
        self.set_stock(product, 0)
        product.action_check_and_restock()
        self.assertTrue(self.smart_pos(product))
        product.sudo().risk_level = "low"
        self.assertFalse(Digest._cron_send_digest())
        product.sudo().risk_level = "high"
        mails = Digest._cron_send_digest()
        self.assertIn("SI Digest Pending", str(mails.mapped("body_html")))

    # E2.4 (#14): ten products of one vendor from the cron end up on one PO
    def test_cron_ten_products_one_po(self):
        products = self.env["product.template"]
        for i in range(10):
            products |= self.make_product(f"SI Ten {i}")
        self.env["product.template"]._cron_auto_restock()
        orders = self.smart_pos(products)
        self.assertEqual(len(orders), 1)
        self.assertEqual(len(orders.order_line), 10)
        self.env["product.template"]._cron_auto_restock()
        self.assertEqual(len(self.smart_pos(products).order_line), 10)
