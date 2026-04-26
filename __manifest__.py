{
    "name": "AI-Powered Smart Inventory",
    "version": "2.0",
    "summary": "AI-Driven Demand Forecasting & Automated Procurement",
    "author": "Naim Reza",
    "depends": ["base", "stock", "sale_management", "purchase", "mail", "sale_purchase", "sale_stock", "contacts"],
    "data": [
        "security/ir.model.access.csv",
        "data/cron.xml",
        "views/product_views.xml",
        "views/dashboard_views.xml",
        "views/menu.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Plus+Jakarta+Sans:wght@300;400;600;800&display=swap",
            "smart_inventory/static/src/js/dashboard.js",
            "smart_inventory/static/src/xml/dashboard.xml",
            "smart_inventory/static/src/css/dashboard.css",
        ],
    },
    "installable": True,
}