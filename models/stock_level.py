from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import float_compare


class SmartInventoryStockLevel(models.Model):
    """Forecast and restock levels per product and warehouse (E3.1)."""
    _name = "smart.inventory.stock.level"
    _description = "Smart Inventory Stock Level"
    _order = "warehouse_id, product_id"

    product_id = fields.Many2one("product.product", required=True, ondelete="cascade", index=True)
    product_tmpl_id = fields.Many2one(related="product_id.product_tmpl_id", store=True, index=True)
    warehouse_id = fields.Many2one("stock.warehouse", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="warehouse_id.company_id", store=True)
    forecast = fields.Float(string="Forecast Demand")
    daily_demand = fields.Float()
    sigma = fields.Float(string="Daily Demand Std Dev")
    lead_time = fields.Integer(string="Lead Time (days)")
    safety_stock = fields.Float()
    reorder_point = fields.Float()
    min_qty_override = fields.Float(
        string="Low Stock Alert (warehouse)",
        help="Overrides the product's Low Stock Alert for this warehouse in manual mode. 0 = use product value.")
    qty_available = fields.Float(compute="_compute_quantities", digits="Product Unit")
    usable_qty = fields.Float(compute="_compute_quantities", digits="Product Unit")
    min_level = fields.Float(string="Restock Below", compute="_compute_quantities")
    excess_qty = fields.Float(string="Excess", compute="_compute_quantities", digits="Product Unit")

    _product_warehouse_uniq = models.Constraint(
        "UNIQUE(product_id, warehouse_id)", "One stock level per product and warehouse.")

    @api.depends("product_id", "warehouse_id", "reorder_point", "safety_stock", "min_qty_override")
    def _compute_quantities(self):
        for rec in self:
            product = rec.product_id.with_context(warehouse_id=rec.warehouse_id.id)
            expired = rec.product_id._si_expired_qty(rec.warehouse_id).get(rec.product_id.id, 0.0)
            rec.qty_available = product.qty_available
            rec.usable_qty = product.qty_available - expired
            rec.min_level = rec._min_level()
            promised = sum(self.env["smart.inventory.transfer.suggestion"].search([
                ("product_id", "=", rec.product_id.id), ("source_warehouse_id", "=", rec.warehouse_id.id),
                ("state", "=", "open")]).mapped("quantity"))
            rec.excess_qty = max(rec.usable_qty - rec.min_level - rec.safety_stock - promised, 0.0)

    def _min_level(self):
        self.ensure_one()
        tmpl = self.product_id.product_tmpl_id
        if tmpl.reorder_mode == "auto":
            return self.reorder_point
        return self.min_qty_override or float(tmpl.low_stock_threshold)

    @api.model
    def _si_refresh(self, variants, warehouses, horizon):
        """Recompute forecast, safety stock and reorder point per (variant, warehouse)."""
        engine = self.env["smart.inventory.forecast.engine"]
        today = engine._today()
        existing = {(lvl.product_id.id, lvl.warehouse_id.id): lvl for lvl in self.search([
            ("product_id", "in", variants.ids), ("warehouse_id", "in", warehouses.ids)])}
        lead_times = {v.id: v._si_lead_time() for v in variants}
        to_create = []
        for warehouse in warehouses:
            sales = engine._daily_sales(variants, 90, warehouse=warehouse)
            for variant in variants:
                stats = engine._baseline(sales.get(variant.id, {}), horizon, end=today)
                lead = lead_times[variant.id]
                reorder_point, safety = engine._reorder_point(
                    stats["daily"], stats["sigma"], lead, variant.product_tmpl_id._si_service_level())
                vals = {
                    "forecast": stats["forecast"], "daily_demand": stats["daily"], "sigma": stats["sigma"],
                    "lead_time": lead, "safety_stock": safety, "reorder_point": reorder_point,
                }
                level = existing.get((variant.id, warehouse.id))
                if level:
                    level.write(vals)
                else:
                    to_create.append(dict(vals, product_id=variant.id, warehouse_id=warehouse.id))
        return self.create(to_create)

    @api.model
    def _si_suggest_transfer(self, product, warehouse, qty):
        """Cover `qty` with a transfer from a warehouse with excess stock instead of buying (E3.2).
        Open suggestions to this warehouse count as covered. Returns the quantity still to buy."""
        Suggestion = self.env["smart.inventory.transfer.suggestion"]
        pending = sum(Suggestion.search([("product_id", "=", product.id),
                                         ("dest_warehouse_id", "=", warehouse.id),
                                         ("state", "=", "open")]).mapped("quantity"))
        qty -= pending
        if float_compare(qty, 0.0, precision_digits=4) <= 0:
            return 0.0
        sources = self.search([
            ("product_id", "=", product.id),
            ("warehouse_id", "!=", warehouse.id),
            ("company_id", "=", warehouse.company_id.id),
        ])
        source = sources.filtered(
            lambda s: float_compare(s.excess_qty, qty, precision_digits=4) >= 0
        ).sorted("excess_qty", reverse=True)[:1]
        if not source:
            return qty
        Suggestion.create({
            "product_id": product.id,
            "source_warehouse_id": source.warehouse_id.id,
            "dest_warehouse_id": warehouse.id,
            "quantity": qty,
        })
        source.invalidate_recordset(["excess_qty"])
        return 0.0


class SmartInventoryTransferSuggestion(models.Model):
    _name = "smart.inventory.transfer.suggestion"
    _description = "Smart Inventory Transfer Suggestion"
    _order = "create_date desc, id desc"

    product_id = fields.Many2one("product.product", required=True, ondelete="cascade")
    source_warehouse_id = fields.Many2one("stock.warehouse", required=True, ondelete="cascade")
    dest_warehouse_id = fields.Many2one("stock.warehouse", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="dest_warehouse_id.company_id", store=True)
    quantity = fields.Float(required=True, digits="Product Unit")
    state = fields.Selection(
        [("open", "Open"), ("done", "Transfer Created"), ("cancel", "Cancelled")],
        default="open", required=True)
    picking_id = fields.Many2one("stock.picking", string="Transfer", readonly=True)

    def action_create_transfer(self):
        for rec in self.filtered(lambda r: r.state == "open"):
            source, dest = rec.source_warehouse_id, rec.dest_warehouse_id
            if not source.int_type_id:
                raise UserError(_("Warehouse %s has no internal transfer operation type.", source.name))
            picking = self.env["stock.picking"].create({
                "picking_type_id": source.int_type_id.id,
                "location_id": source.lot_stock_id.id,
                "location_dest_id": dest.lot_stock_id.id,
                "origin": _("Smart Inventory transfer suggestion"),
                "move_ids": [(0, 0, {
                    "product_id": rec.product_id.id,
                    "product_uom_qty": rec.quantity,
                    "product_uom": rec.product_id.uom_id.id,
                    "location_id": source.lot_stock_id.id,
                    "location_dest_id": dest.lot_stock_id.id,
                })],
            })
            picking.action_confirm()
            rec.write({"state": "done", "picking_id": picking.id})
        if len(self) == 1 and self.picking_id:
            return {
                "type": "ir.actions.act_window",
                "res_model": "stock.picking",
                "res_id": self.picking_id.id,
                "views": [[False, "form"]],
            }
        return True

    def action_cancel(self):
        self.filtered(lambda r: r.state == "open").write({"state": "cancel"})
        return True
