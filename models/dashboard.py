from collections import defaultdict
from datetime import timedelta

from odoo import api, fields, models

from .product import SMART_ORIGIN

RISK_ORDER = {"high": 0, "medium": 1, "low": 2}
TOTAL_COLUMNS = {"Shortage", "Stock Value", "Predicted Demand", "On Hand"}


class SmartInventoryDashboard(models.AbstractModel):
    """Server-side data for the OWL dashboard (E4.1, E4.3) and the forecast chart (E4.2)."""
    _name = "smart.inventory.dashboard"
    _description = "Smart Inventory Dashboard"

    # ------------------------------------------------------------------
    # Filters
    # ------------------------------------------------------------------
    @api.model
    def _product_domain(self, filters):
        domain = [("is_storable", "=", True)]
        if filters.get("categ_id"):
            domain.append(("categ_id", "child_of", int(filters["categ_id"])))
        if filters.get("vendor_id"):
            vendor = int(filters["vendor_id"])
            domain += ["|", ("preferred_vendor_id", "=", vendor), ("seller_ids.partner_id", "=", vendor)]
        if filters.get("risk_level"):
            domain.append(("risk_level", "=", filters["risk_level"]))
        return domain

    @api.model
    def _warehouse(self, filters):
        """Filtered warehouse, ignoring stale ids (deleted, or another company's)."""
        if not filters.get("warehouse_id"):
            return self.env["stock.warehouse"]
        return self.env["stock.warehouse"].search([
            ("id", "=", int(filters["warehouse_id"])), ("company_id", "in", self.env.companies.ids)])

    @api.model
    def get_filter_options(self):
        categories = self.env["product.category"].search_read([], ["display_name"], order="complete_name")
        warehouses = self.env["stock.warehouse"].search_read(
            [("company_id", "in", self.env.companies.ids)], ["name"])
        vendors = self.env["product.supplierinfo"].search([]).partner_id | \
            self.env["product.template"].search([("preferred_vendor_id", "!=", False)]).preferred_vendor_id
        return {
            "categories": [{"id": c["id"], "name": c["display_name"]} for c in categories],
            "warehouses": [{"id": w["id"], "name": w["name"]} for w in warehouses],
            "vendors": [{"id": v.id, "name": v.display_name} for v in vendors.sorted("name")],
            "risk_levels": [{"id": "high", "name": "High"}, {"id": "medium", "name": "Medium"},
                            {"id": "low", "name": "Low"}],
        }

    # ------------------------------------------------------------------
    # KPIs and lists
    # ------------------------------------------------------------------
    @api.model
    def get_dashboard_data(self, period_days=30, filters=None):
        filters = filters or {}
        period_days = int(period_days) if int(period_days or 0) > 0 else 30
        warehouse = self._warehouse(filters)
        Product = self.env["product.template"]
        if warehouse:
            Product = Product.with_context(warehouse_id=warehouse.id)
        products = Product.search(self._product_domain(filters))
        variants = products.product_variant_ids
        start = fields.Datetime.now() - timedelta(days=period_days)

        kpis = self._kpis(products, variants, warehouse, start, period_days)
        low = products.filtered("is_low_stock").sorted(lambda p: p.forecast_shortage, reverse=True)
        recommendations = products.filtered(
            lambda p: p.risk_level != "low" or p.predicted_demand > p.usable_qty
        ).sorted(lambda p: (RISK_ORDER.get(p.risk_level, 3), -p.forecast_shortage))[:5]

        # Smart Inventory users may lack Purchase rights; show restock POs of their companies only.
        PO = self.env["purchase.order"].sudo()
        smart = PO._si_smart_domain() + [("company_id", "in", self.env.companies.ids)]
        return {
            "kpis": kpis,
            "totalProducts": len(products),
            "lowStockCount": len(low),
            "pendingOrders": PO.search_count(smart + [("state", "=", "draft")]),
            "deadStockCount": len(products.filtered("is_dead_stock")),
            "lowStockProducts": [self._product_row(p) for p in low[:50]],
            "aiRecommendations": [self._product_row(p) for p in recommendations],
            "recentActivities": PO.search_read(
                smart, ["name", "partner_id", "amount_total", "state", "date_order", "si_approval_state"],
                limit=5, order="date_order desc"),
            "pendingApprovals": PO.search_read(
                smart + [("si_approval_state", "=", "pending")],
                ["name", "partner_id", "amount_untaxed", "currency_id"], limit=10, order="date_order desc"),
            "expiringLots": self._expiring_lots(variants, warehouse),
            "transferSuggestions": self.env["smart.inventory.transfer.suggestion"].search_read(
                [("state", "=", "open")] + ([("product_id", "in", variants.ids)] if filters else []),
                ["product_id", "source_warehouse_id", "dest_warehouse_id", "quantity"], limit=10),
            "isManager": self.env.user.has_group("smart_inventory.group_smart_inventory_manager"),
            "smartOrigin": SMART_ORIGIN,
        }

    @api.model
    def _product_row(self, p):
        return {
            "id": p.id,
            "name": p.display_name,
            "qty_available": round(p.usable_qty, 2),
            "low_stock_threshold": round(p.min_stock_level, 2),
            "forecast_shortage": round(p.forecast_shortage, 2),
            "predicted_demand": round(p.predicted_demand, 2),
            "days_of_cover": round(p.days_of_cover, 1),
            "risk_level": p.risk_level or "low",
            "ai_insight": p.ai_insight or "",
            "abc_class": p.abc_class or "",
        }

    @api.model
    def _kpis(self, products, variants, warehouse, start, period_days):
        stock_value = sum(products.mapped("si_stock_value"))

        move_domain = [
            ("product_id", "in", variants.ids), ("state", "=", "done"), ("date", ">=", start),
            ("location_dest_id.usage", "=", "customer"), ("location_id.usage", "=", "internal"),
            ("company_id", "in", self.env.companies.ids),
        ]
        if warehouse:
            move_domain.append(("location_id", "child_of", warehouse.view_location_id.id))
        delivered = self.env["stock.move"].sudo()._read_group(move_domain, ["product_id"], ["product_qty:sum"])
        cogs = sum(product.standard_price * qty for product, qty in delivered)
        daily_cogs = cogs / period_days

        line_domain = [("product_id", "in", variants.ids), ("state", "=", "sale"),
                       ("company_id", "in", self.env.companies.ids), ("order_id.date_order", ">=", start)]
        if warehouse:
            line_domain.append(("order_id.warehouse_id", "=", warehouse.id))
        lines = self.env["sale.order.line"].sudo().search_read(line_domain, ["product_uom_qty", "qty_delivered"])
        ordered = sum(line["product_uom_qty"] for line in lines)
        served = sum(min(line["qty_delivered"], line["product_uom_qty"]) for line in lines)

        return {
            "stockValue": round(stock_value, 2),
            "currency": self.env.company.currency_id.symbol or "",
            "turnover": round(cogs / stock_value, 2) if stock_value else 0.0,
            "daysOfCover": round(stock_value / daily_cogs, 1) if daily_cogs else 0.0,
            "fillRate": round(100.0 * served / ordered, 1) if ordered else 100.0,
            "stockouts": len(products.filtered(lambda p: p.usable_qty <= 0)),
            "periodDays": period_days,
        }

    @api.model
    def _expiring_lots(self, variants, warehouse):
        if "expiration_date" not in self.env["stock.lot"]._fields:
            return []
        days = self.env["smart.inventory.config"].get("expiry_alert_days")
        now = fields.Datetime.now()
        domain = [
            ("product_id", "in", variants.ids),
            ("location_id.usage", "=", "internal"),
            ("quantity", ">", 0),
            ("lot_id.expiration_date", "!=", False),
            ("lot_id.expiration_date", "<=", now + timedelta(days=days)),
        ]
        if warehouse:
            domain.append(("location_id", "child_of", warehouse.view_location_id.id))
        groups = self.env["stock.quant"].sudo()._read_group(domain, ["lot_id"], ["quantity:sum"])
        rows = [{
            "id": lot.id,
            "lot": lot.name,
            "product": lot.product_id.display_name,
            "quantity": round(qty, 2),
            "expiration_date": fields.Datetime.to_string(lot.expiration_date),
            "expired": lot.expiration_date <= now,
        } for lot, qty in groups]
        return sorted(rows, key=lambda r: r["expiration_date"])[:20]

    # ------------------------------------------------------------------
    # Forecast chart (E4.2)
    # ------------------------------------------------------------------
    @api.model
    def get_product_chart(self, product_tmpl_id, days=90):
        tmpl = self.env["product.template"].browse(int(product_tmpl_id)).exists()
        if not tmpl:
            return {}
        variants = tmpl.product_variant_ids
        engine = self.env["smart.inventory.forecast.engine"]
        today = engine._today()
        start = today - timedelta(days=days)
        labels = [start + timedelta(days=i) for i in range(days + 1)]

        sales = engine._daily_sales(variants, days + 1, end=today + timedelta(days=1))
        daily_sales = defaultdict(float)
        for per_day in sales.values():
            for day, qty in per_day.items():
                daily_sales[day] += qty

        # Stock at end of each day, rebuilt backwards from the current quantity.
        moves = self.env["stock.move"].sudo().search([
            ("product_id", "in", variants.ids), ("state", "=", "done"),
            ("company_id", "in", self.env.companies.ids),
            ("date", ">=", engine._day_start_utc(start)),
        ])
        net = defaultdict(float)
        for move in moves:
            src_in = move.location_id.usage == "internal"
            dest_in = move.location_dest_id.usage == "internal"
            if src_in != dest_in:
                net[engine._local_date(move.date)] += move.product_qty if dest_in else -move.product_qty
        stock, running = {}, tmpl.qty_available
        for day in reversed(labels):
            stock[day] = running
            running -= net.get(day, 0.0)

        forecast_line = {}
        for fc in tmpl.forecast_ids.filtered(lambda f: f.date and f.date_end >= start):
            per_day = fc.predicted / fc.horizon if fc.horizon else 0.0
            for i in range(fc.horizon):
                forecast_line.setdefault(fc.date + timedelta(days=i), per_day)

        horizon = self.env["smart.inventory.config"].get("forecast_horizon")
        future = [today + timedelta(days=i) for i in range(1, horizon + 1)]
        future_daily = tmpl.predicted_demand / horizon if horizon else 0.0
        all_labels = labels + future
        return {
            "product": tmpl.display_name,
            "labels": [fields.Date.to_string(d) for d in all_labels],
            "sales": [round(daily_sales.get(d, 0.0), 2) if d <= today else None for d in all_labels],
            "stock": [round(stock[d], 2) if d in stock else None for d in all_labels],
            "forecast": [round(forecast_line[d], 2) if d in forecast_line
                         else (round(future_daily, 2) if d > today else None) for d in all_labels],
            "minLevel": round(tmpl.min_stock_level, 2),
        }

    # ------------------------------------------------------------------
    # Exports (E4.5)
    # ------------------------------------------------------------------
    EXPORTS = {
        "shortage": "Stock Shortages",
        "forecast": "Demand Forecast",
        "dead": "Dead and Slow-Moving Stock",
    }

    @api.model
    def _export_rows(self, kind, filters=None):
        """Return (title, headers, rows) for an export; the same rows feed XLSX and PDF."""
        if kind not in self.EXPORTS:
            raise ValueError(kind)
        filters = filters or {}
        warehouse = self._warehouse(filters)
        Product = self.env["product.template"]
        if warehouse:
            Product = Product.with_context(warehouse_id=warehouse.id)
        products = Product.search(self._product_domain(filters))
        if kind == "shortage":
            products = products.filtered("is_low_stock").sorted(lambda p: p.forecast_shortage, reverse=True)
            headers = ["Product", "Usable Qty", "Restock Below", "Shortage", "Risk"]
            rows = [[p.display_name, p.usable_qty, p.min_stock_level, p.forecast_shortage,
                     dict(p._fields["risk_level"].selection).get(p.risk_level, "")] for p in products]
        elif kind == "forecast":
            products = products.sorted(lambda p: p.predicted_demand, reverse=True)
            headers = ["Product", "Usable Qty", "Avg Daily Demand", "Predicted Demand", "Safety Stock",
                       "Reorder Point", "Days of Cover", "MAPE %", "Source"]
            rows = [[p.display_name, p.usable_qty, p.avg_daily_demand, p.predicted_demand, p.safety_stock,
                     p.reorder_point, p.days_of_cover, p.forecast_mape, p.forecast_source or ""]
                    for p in products]
        else:
            products = products.filtered("is_dead_stock").sorted(lambda p: p.si_stock_value, reverse=True)
            headers = ["Product", "On Hand", "Stock Value", "Last Sale"]
            rows = [[p.display_name, p.qty_available, p.si_stock_value,
                     fields.Datetime.to_string(p.last_sale_date) if p.last_sale_date else "Never"]
                    for p in products]
        totals = ["Total"] + [sum(r[i] for r in rows) if headers[i] in TOTAL_COLUMNS else ""
                              for i in range(1, len(headers))]
        return self.EXPORTS[kind], headers, rows, totals


class ReportSmartInventoryStockList(models.AbstractModel):
    _name = "report.smart_inventory.report_stock_list"
    _description = "Smart Inventory Stock List Report"

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        title, headers, rows, totals = self.env["smart.inventory.dashboard"]._export_rows(
            data.get("kind", "shortage"), data.get("filters"))

        def cells(row):
            return [{"text": f"{v:,.2f}" if isinstance(v, float) else str(v),
                     "num": isinstance(v, (int, float))} for v in row]
        return {"title": title, "headers": headers, "rows": [cells(r) for r in rows],
                "totals": cells(totals), "company": self.env.company, "date": fields.Datetime.now()}
