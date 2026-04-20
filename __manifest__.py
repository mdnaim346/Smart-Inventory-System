{
    "name": "Smart Inventory System",
    "version": "1.0",
    "summary": "Advanced Inventory & Sales Automation",
    "author": "Naim Reza",
    "depends": ["base", "stock", "sale", "mail", "purchase"],
    "data": [
        "security/ir.model.access.csv",
        "data/cron.xml",
        "views/product_views.xml",
        "views/dashboard_views.xml",
        "views/menu.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "smart_inventory/static/src/js/dashboard.js",
            "smart_inventory/static/src/xml/dashboard.xml",
            "smart_inventory/static/src/css/dashboard.css",
        ],
    },
    "installable": True,
}