from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestUi(HttpCase):
    """Smoke test: the OWL dashboard and forecast chart render without JS errors."""

    def test_dashboard_and_chart(self):
        product = self.env["product.template"].create({
            "name": "SI UI Product", "type": "consu", "is_storable": True,
            "low_stock_threshold": 100000, "auto_restock": False,
        })
        code = """
            const wait = (sel) => new Promise((resolve, reject) => {
                const start = Date.now();
                const tick = () => {
                    const el = document.querySelector(sel);
                    if (el) { return resolve(el); }
                    if (Date.now() - start > 20000) { return reject(new Error("timeout " + sel)); }
                    setTimeout(tick, 100);
                };
                tick();
            });
            (async () => {
                await wait(".si-filter-bar");
                await wait(".si-kpi-row .metric-value");
                (await wait("#btn_chart_product_%(id)s")).click();
                await wait(".si-forecast-chart canvas");
                console.log("test successful");
            })().catch((e) => console.error(e.message));
        """ % {"id": product.id}
        self.browser_js("/odoo/action-smart_inventory.smart_inventory_dashboard_action", code, login="admin")
