from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase


class SmartInventoryCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.company.write({
            "si_block_out_of_stock": True,
            "si_auto_deliver": True,
            "si_auto_invoice": True,
            "si_create_backorder": True,
        })
        cls.warehouse = cls.env["stock.warehouse"].search([("company_id", "=", cls.company.id)], limit=1)
        cls.stock_location = cls.warehouse.lot_stock_id
        cls.vendor = cls.env["res.partner"].create({"name": "SI Vendor A", "supplier_rank": 1})
        cls.vendor_b = cls.env["res.partner"].create({"name": "SI Vendor B", "supplier_rank": 1})
        cls.customer = cls.env["res.partner"].create({"name": "SI Customer"})
        cls.category = cls.env["product.category"].create({"name": "SI Test Category"})
        cls.set_param("replenish_mode", "po")
        cls.set_param("po_approval_amount", "0")
        cls.set_param("ai_enabled", "False")

    @classmethod
    def set_param(cls, key, value):
        cls.env["ir.config_parameter"].sudo().set_param("smart_inventory." + key, value)

    @classmethod
    def make_product(cls, name, vendor=None, price=8.0, delay=3, **vals):
        values = {
            "name": name,
            "type": "consu",
            "is_storable": True,
            "standard_price": 10.0,
            "list_price": 20.0,
            "invoice_policy": "order",
            "categ_id": cls.category.id,
            "low_stock_threshold": 10,
            "restock_quantity": 50,
            "auto_restock": True,
            "seller_ids": [(0, 0, {"partner_id": (vendor or cls.vendor).id, "price": price, "delay": delay})],
        }
        values.update(vals)
        return cls.env["product.template"].create(values)

    @classmethod
    def set_stock(cls, template, qty, location=None):
        """Set the absolute on-hand quantity of the template's variant at `location`."""
        cls.env["stock.quant"].with_context(inventory_mode=True).create({
            "product_id": template.product_variant_id.id,
            "location_id": (location or cls.stock_location).id,
            "inventory_quantity": qty,
        }).action_apply_inventory()
        template.invalidate_recordset()

    @classmethod
    def past_sale(cls, template, qty, days_ago, warehouse=None):
        """A confirmed sale in the past, without running the confirmation automation."""
        order = cls.env["sale.order"].create({
            "partner_id": cls.customer.id,
            "warehouse_id": (warehouse or cls.warehouse).id,
            "order_line": [(0, 0, {"product_id": template.product_variant_id.id, "product_uom_qty": qty})],
        })
        order.write({"state": "sale", "date_order": fields.Datetime.now() - timedelta(days=days_ago)})
        return order

    def smart_pos(self, templates):
        return self.env["purchase.order"].search([
            ("si_is_smart_restock", "=", True),
            ("order_line.product_id", "in", templates.product_variant_ids.ids),
            ("state", "!=", "cancel"),
        ])

    def patch_daily_sales(self, per_day, days=90):
        """Pretend every variant sold `per_day` units on each of the last `days` days."""
        engine_cls = type(self.env["smart.inventory.forecast.engine"])
        today = fields.Date.context_today(self.env.user)

        def fake(engine, products, window, warehouse=None, end=None):
            end = end or today
            series = {end - timedelta(days=i): float(per_day) for i in range(1, min(window, days) + 1)}
            return {p.id: dict(series) for p in products}

        return patch.object(engine_cls, "_daily_sales", fake)
