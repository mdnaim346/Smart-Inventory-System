{
    "name": "AI-Powered Smart Inventory",
    "version": "19.0.3.2.0",
    "summary": "Demand forecasting, smart replenishment and stock health, with optional Claude AI",
    "description": """
Smart Inventory tells you what to buy, how much, from whom and when, explains why,
and prepares the purchase orders and transfers for your approval.

* Statistical demand forecast per product and warehouse, with accuracy tracking (MAPE)
* Optional Claude AI adjustment and plain-language risk insight, capped and budgeted
* Reorder point and safety stock from vendor lead time and service level (ABC based)
* One draft purchase order per vendor and warehouse, vendor minimum quantity and pack size
* Transfer suggestions from warehouses with excess stock before buying
* Approval of large restock orders by a Smart Inventory Manager
* Dead stock, lot expiry, ABC/XYZ classes
* KPI dashboard, forecast chart, XLSX/PDF exports, daily risk e-mail digest
* Configurable sales automation: block out-of-stock sales, auto delivery, auto invoice
* Restock suggestions inbox (approve, edit, reject with reason) and a weekly restock budget
* Seasons and promotions in the forecast; new products forecast like a similar product
* Vendor scorecard, overstock alerts, lost-sales log and a money view on the dashboard
* ABC cycle counting, stock adjustment reasons and report, First Expired First Out picking
* Dead stock actions: discount, return to vendor, move to another warehouse
* Telegram and webhook (WhatsApp or SMS gateway) alerts
""",
    "author": "Naim Reza",
    "maintainer": "Naim Reza",
    "website": "https://github.com/mdnaim346/Smart-Inventory-System",
    "support": "https://github.com/mdnaim346/Smart-Inventory-System/issues",
    "license": "LGPL-3",
    "category": "Inventory/Inventory",
    "depends": ["base", "stock", "sale_management", "purchase", "purchase_stock", "mail",
                "sale_purchase", "sale_stock", "contacts"],
    # `anthropic` is optional: without it the module uses the statistical baseline only.
    "external_dependencies": {"python": ["xlsxwriter"]},
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/cron.xml",
        "data/mail_template.xml",
        "data/adjustment_reasons.xml",
        "report/reports.xml",
        "views/product_views.xml",
        "views/forecast_views.xml",
        "views/purchase_views.xml",
        "views/res_config_settings_views.xml",
        "views/dashboard_views.xml",
        "views/inventory_ops_views.xml",
        "views/menu.xml",
    ],
    "demo": [
        "demo/demo.xml",
    ],
    "assets": {
        "web.assets_backend": [
            # OLD: external font request on every backend page load (fails offline, privacy)
            # "https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Plus+Jakarta+Sans:wght@300;400;600;800&display=swap",
            "smart_inventory/static/src/js/dashboard.js",
            "smart_inventory/static/src/js/forecast_chart.js",
            "smart_inventory/static/src/xml/dashboard.xml",
            "smart_inventory/static/src/xml/forecast_chart.xml",
            "smart_inventory/static/src/css/dashboard.css",
        ],
    },
    "images": ["static/description/banner.png"],
    "application": True,
    "installable": True,
}
