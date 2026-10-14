import json

from odoo.tests import HttpCase, tagged

from .common import SmartInventoryCase


@tagged("post_install", "-at_install")
class TestDashboardData(SmartInventoryCase):
    """E4.1 KPIs, E4.2 chart, E4.3 filters, E4.4 digest, E4.5 exports"""

    def setUp(self):
        super().setUp()
        self.dashboard = self.env["smart.inventory.dashboard"]
        self.filters = {"categ_id": self.category.id}
        self.low = self.make_product("SI Dash Low")
        self.ok = self.make_product("SI Dash OK")
        self.set_stock(self.ok, 100)

    def test_kpis_and_lists(self):
        order = self.past_sale(self.ok, 10, days_ago=1)
        self.assertEqual(order.order_line.qty_delivered, 0)
        data = self.dashboard.get_dashboard_data(30, self.filters)
        self.assertEqual(data["totalProducts"], 2)
        self.assertEqual(data["lowStockCount"], 1)
        self.assertEqual([p["id"] for p in data["lowStockProducts"]], [self.low.id])
        kpis = data["kpis"]
        self.assertEqual(kpis["stockValue"], 1000.0)  # 100 x cost 10
        self.assertEqual(kpis["stockouts"], 1)
        self.assertEqual(kpis["fillRate"], 0.0)
        self.assertEqual(kpis["periodDays"], 30)

    def test_turnover_from_deliveries(self):
        order = self.env["sale.order"].create({
            "partner_id": self.customer.id,
            "order_line": [(0, 0, {"product_id": self.ok.product_variant_id.id, "product_uom_qty": 20})],
        })
        order.action_confirm()  # auto-delivers
        kpis = self.dashboard.get_dashboard_data(30, self.filters)["kpis"]
        self.assertEqual(kpis["stockValue"], 800.0)
        self.assertAlmostEqual(kpis["turnover"], 200.0 / 800.0, places=2)
        self.assertEqual(kpis["fillRate"], 100.0)

    def test_filters(self):
        self.low.risk_level = "high"
        data = self.dashboard.get_dashboard_data(30, dict(self.filters, risk_level="high"))
        self.assertEqual(data["totalProducts"], 1)
        other = self.env["product.category"].create({"name": "SI Empty"})
        self.assertEqual(self.dashboard.get_dashboard_data(30, {"categ_id": other.id})["totalProducts"], 0)
        options = self.dashboard.get_filter_options()
        self.assertIn(self.category.id, [c["id"] for c in options["categories"]])
        self.assertIn(self.vendor.id, [v["id"] for v in options["vendors"]])

    def test_chart_data(self):
        self.past_sale(self.ok, 4, days_ago=2)
        self.ok.action_generate_ai_insights()
        chart = self.dashboard.get_product_chart(self.ok.id)
        n = len(chart["labels"])
        self.assertEqual(n, 91 + 7)
        self.assertTrue(len(chart["sales"]) == len(chart["stock"]) == len(chart["forecast"]) == n)
        self.assertEqual(chart["stock"][90], 100.0)
        self.assertEqual(sum(v for v in chart["sales"] if v), 4.0)

    def test_exports(self):
        title, headers, rows, totals = self.dashboard._export_rows("shortage", self.filters)
        self.assertEqual(title, "Stock Shortages")
        self.assertEqual([r[0] for r in rows], [self.low.display_name])
        self.assertEqual(totals[headers.index("Shortage")], 10.0)
        html = self.env["ir.actions.report"]._render_qweb_html(
            "smart_inventory.report_stock_list", [], data={"kind": "shortage", "filters": self.filters})[0]
        self.assertIn(b"SI Dash Low", html)
        self.assertIn(b"10.00", html)

    def test_digest(self):
        Digest = self.env["smart.inventory.digest"]
        self.env["product.template"].search([("risk_level", "=", "high")]).risk_level = "low"
        self.env["purchase.order"].search(self.env["purchase.order"]._si_smart_domain()).button_cancel()
        self.env.ref("base.user_admin").email = "admin@example.com"
        self.assertFalse(Digest._cron_send_digest())
        self.low.risk_level = "high"
        mails = Digest._cron_send_digest()
        self.assertTrue(mails)
        mail = mails.filtered(lambda m: "SI Dash Low" in str(m.body_html))[:1]
        self.assertTrue(mail)
        self.assertIn("1 high-risk", mail.subject)


@tagged("post_install", "-at_install")
class TestExportController(HttpCase):

    def test_xlsx_download(self):
        self.authenticate("admin", "admin")
        response = self.url_open("/smart_inventory/export/shortage.xlsx?filters=" + json.dumps({}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content[:2], b"PK")  # xlsx is a zip archive
        self.assertEqual(self.url_open("/smart_inventory/export/unknown.xlsx").status_code, 404)
