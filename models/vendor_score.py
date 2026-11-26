from collections import defaultdict
from datetime import timedelta

from odoo import api, fields, models

from .product import _check_manager

SCORE_WINDOW_DAYS = 180
PRICE_WINDOW_DAYS = 90


class SmartInventoryVendorScore(models.Model):
    """Delivery and price behaviour of a vendor over the last 180 days."""
    _name = "smart.inventory.vendor.score"
    _description = "Smart Inventory Vendor Scorecard"
    _order = "score desc, partner_id"
    _rec_name = "partner_id"

    partner_id = fields.Many2one("res.partner", string="Vendor", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one("res.company", required=True, ondelete="cascade")
    order_count = fields.Integer(string="Purchase Orders")
    receipt_count = fields.Integer(string="Receipts")
    on_time_rate = fields.Float(string="On Time (%)", aggregator="avg",
                                help="Receipts done on or before their scheduled date.")
    avg_delay_days = fields.Float(string="Avg Delay (days)", aggregator="avg",
                                  help="Average days late, counting only late receipts.")
    fill_rate = fields.Float(string="Quantity Delivered (%)", aggregator="avg",
                             help="Received quantity / ordered quantity on lines that were due.")
    price_change_pct = fields.Float(
        string="Price Change (%)", aggregator="avg",
        help="Average unit price change, last 90 days against the 90 days before (same products).")
    score = fields.Float(string="Score", aggregator="avg",
                         help="40% on time + 40% quantity delivered + 20% price stability.")
    last_update = fields.Datetime(readonly=True)

    _partner_company_uniq = models.Constraint(
        "UNIQUE(partner_id, company_id)", "One scorecard per vendor and company.")

    @api.model
    def action_refresh(self):
        _check_manager(self.env)
        self._cron_refresh()
        return {"type": "ir.actions.client", "tag": "soft_reload"}

    @api.model
    def _cron_refresh(self):
        for company in self.env["res.company"].search([]):
            self._refresh_company(company)
        return True

    @api.model
    def _refresh_company(self, company):
        now = fields.Datetime.now()
        start = now - timedelta(days=SCORE_WINDOW_DAYS)
        Line = self.env["purchase.order.line"].sudo()
        lines = Line.search([
            ("order_id.state", "=", "purchase"), ("company_id", "=", company.id),
            ("order_id.date_approve", ">=", start), ("display_type", "=", False)])
        pickings = self.env["stock.picking"].sudo().search([
            ("picking_type_code", "=", "incoming"), ("state", "=", "done"), ("company_id", "=", company.id),
            ("date_done", ">=", start), ("purchase_id", "!=", False)])

        stats = defaultdict(lambda: {"orders": set(), "ordered": 0.0, "received": 0.0,
                                     "receipts": 0, "on_time": 0, "late_days": [],
                                     "recent": defaultdict(list), "older": defaultdict(list)})
        price_split = now - timedelta(days=PRICE_WINDOW_DAYS)
        for line in lines:
            vendor = line.order_id.partner_id.commercial_partner_id
            data = stats[vendor]
            data["orders"].add(line.order_id.id)
            if line.date_planned and line.date_planned <= now and line.product_qty:
                data["ordered"] += line.product_qty
                data["received"] += min(line.qty_received, line.product_qty)
            bucket = "recent" if line.order_id.date_approve >= price_split else "older"
            data[bucket][line.product_id.id].append(line.price_unit)
        for picking in pickings:
            data = stats[picking.purchase_id.partner_id.commercial_partner_id]
            data["receipts"] += 1
            late = (picking.date_done.date() - picking.scheduled_date.date()).days if picking.scheduled_date else 0
            if late <= 0:
                data["on_time"] += 1
            else:
                data["late_days"].append(late)

        existing = {rec.partner_id.id: rec for rec in self.search([("company_id", "=", company.id)])}
        for vendor, data in stats.items():
            on_time = 100.0 * data["on_time"] / data["receipts"] if data["receipts"] else 100.0
            fill = 100.0 * data["received"] / data["ordered"] if data["ordered"] else 100.0
            changes = []
            for product_id, recent in data["recent"].items():
                older = data["older"].get(product_id)
                if older:
                    old_avg = sum(older) / len(older)
                    if old_avg:
                        changes.append(100.0 * (sum(recent) / len(recent) - old_avg) / old_avg)
            price_change = sum(changes) / len(changes) if changes else 0.0
            price_score = max(0.0, 100.0 - max(price_change, 0.0) * 5)
            vals = {
                "order_count": len(data["orders"]),
                "receipt_count": data["receipts"],
                "on_time_rate": round(on_time, 1),
                "avg_delay_days": round(sum(data["late_days"]) / len(data["late_days"]), 1) if data["late_days"] else 0.0,
                "fill_rate": round(fill, 1),
                "price_change_pct": round(price_change, 1),
                "score": round(0.4 * on_time + 0.4 * fill + 0.2 * price_score, 1),
                "last_update": now,
            }
            if vendor.id in existing:
                existing[vendor.id].write(vals)
            else:
                self.create(dict(vals, partner_id=vendor.id, company_id=company.id))
