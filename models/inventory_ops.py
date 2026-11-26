from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .product import _check_manager


class SmartInventoryAdjustmentReason(models.Model):
    _name = "smart.inventory.adjustment.reason"
    _description = "Stock Adjustment Reason"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)


class StockQuant(models.Model):
    _inherit = "stock.quant"

    si_reason_id = fields.Many2one("smart.inventory.adjustment.reason", string="Adjustment Reason")
    si_abc_class = fields.Selection(related="product_id.product_tmpl_id.abc_class", string="ABC Class")

    @api.model
    def _get_inventory_fields_write(self):
        return super()._get_inventory_fields_write() + ["si_reason_id"]

    def _get_inventory_move_values(self, qty, location_id, location_dest_id, package_id=False, package_dest_id=False):
        vals = super()._get_inventory_move_values(qty, location_id, location_dest_id, package_id, package_dest_id)
        if self.si_reason_id:
            vals["si_reason_id"] = self.si_reason_id.id
        return vals

    def _apply_inventory(self, date=None):
        if self.env["smart.inventory.config"].get("require_adjust_reason"):
            missing = self.filtered(lambda q: q.inventory_quantity_set and not q.si_reason_id
                                    and q.product_uom_id.compare(q.inventory_diff_quantity, 0) != 0)
            if missing:
                raise UserError(_("Choose an adjustment reason for: %s",
                                  ", ".join(missing.product_id.mapped("display_name"))))
        return super()._apply_inventory(date)

    def action_clear_inventory_quantity(self):
        res = super().action_clear_inventory_quantity()
        self.si_reason_id = False
        return res

    @api.model
    def action_si_cycle_count(self):
        """Physical Inventory list showing the counts that are due."""
        action = self.action_view_inventory()
        action["name"] = _("Cycle Count")
        action["context"] = dict(action.get("context") or {}, search_default_to_count=1)
        return action

    @api.model
    def _cron_schedule_cycle_counts(self):
        """Bring the next count date of each quant forward to its ABC interval
        (A more often than C). Dates already earlier, set by hand or by Odoo, are kept."""
        config = self.env["smart.inventory.config"]
        if not config.get("cycle_count"):
            return True
        intervals = {cls: config.get("cycle_days_" + cls) for cls in ("a", "b", "c")}
        today = fields.Date.context_today(self)
        quants = self.search([("location_id.usage", "=", "internal"), ("product_id.is_storable", "=", True),
                              ("quantity", "!=", 0)])
        for quant in quants:
            interval = intervals.get(quant.product_id.abc_class or "c") or intervals["c"]
            last = quant.last_count_date or (quant.in_date.date() if quant.in_date else today)
            due = max(last + timedelta(days=interval), today)
            if not quant.inventory_date or due < quant.inventory_date:
                quant.inventory_date = due
        return True


class StockMove(models.Model):
    _inherit = "stock.move"

    si_reason_id = fields.Many2one("smart.inventory.adjustment.reason", string="Adjustment Reason",
                                   index="btree_not_null", readonly=True)
    si_adjust_qty = fields.Float(
        string="Adjusted Quantity", compute="_compute_si_adjust", store=True, digits="Product Unit",
        help="Positive when stock was found, negative when stock was lost.")
    si_adjust_value = fields.Float(string="Adjusted Value", compute="_compute_si_adjust", store=True)

    @api.depends("is_inventory", "product_qty", "location_dest_id.usage")
    def _compute_si_adjust(self):
        for move in self:
            if not move.is_inventory:
                move.si_adjust_qty = move.si_adjust_value = 0.0
                continue
            sign = 1 if move.location_dest_id.usage == "internal" else -1
            move.si_adjust_qty = sign * move.product_qty
            move.si_adjust_value = move.si_adjust_qty * move.product_id.standard_price


class SmartInventoryStockout(models.Model):
    """One product out of stock on one day: the sales likely lost that day."""
    _name = "smart.inventory.stockout"
    _description = "Smart Inventory Stockout Day"
    _order = "date desc, id desc"

    date = fields.Date(required=True, index=True)
    product_tmpl_id = fields.Many2one("product.template", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one("res.company", required=True)
    lost_qty = fields.Float(string="Lost Demand", digits="Product Unit")
    lost_value = fields.Float(string="Lost Sales Value")

    _day_uniq = models.Constraint(
        "UNIQUE(date, product_tmpl_id, company_id)", "One stockout row per product, company and day.")

    @api.model
    def _cron_log_stockouts(self):
        today = self.env["smart.inventory.forecast.engine"]._today()
        default_company = self.env.company
        for company in self.env["res.company"].search([]):
            templates = self.env["product.template"].with_company(company).with_context(
                allowed_company_ids=[company.id]).search([
                    ("is_storable", "=", True), ("avg_daily_demand", ">", 0),
                    ("company_id", "in", [False, company.id])])
            templates = templates._si_used_by(company, default=company == default_company)
            done = set(self.search([("date", "=", today), ("company_id", "=", company.id)]).product_tmpl_id.ids)
            self.create([{
                "date": today,
                "product_tmpl_id": tmpl.id,
                "company_id": company.id,
                "lost_qty": tmpl.avg_daily_demand,
                "lost_value": tmpl.avg_daily_demand * tmpl.list_price,
            } for tmpl in templates if tmpl.usable_qty <= 0 and tmpl.id not in done])
        return True


class SmartInventoryDeadStockWizard(models.TransientModel):
    """Act on dead stock: discount it, send it back to the vendor or move it to another warehouse."""
    _name = "smart.inventory.dead.stock.wizard"
    _description = "Dead Stock Action"

    product_tmpl_ids = fields.Many2many("product.template", "smart_inventory_dead_stock_wiz_rel",
                                        "wizard_id", "product_tmpl_id", string="Products", required=True)
    action = fields.Selection([
        ("discount", "Discount on a pricelist"),
        ("return", "Return to vendor"),
        ("transfer", "Move to another warehouse"),
    ], required=True, default="discount")
    pricelist_id = fields.Many2one("product.pricelist")
    discount = fields.Float(string="Discount (%)", default=20.0)
    warehouse_id = fields.Many2one(
        "stock.warehouse", string="From Warehouse",
        default=lambda self: self.env["stock.warehouse"].search([("company_id", "=", self.env.company.id)], limit=1))
    dest_warehouse_id = fields.Many2one("stock.warehouse", string="To Warehouse")
    vendor_id = fields.Many2one("res.partner", string="Vendor")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self.env.context.get("active_model") == "product.template" and "product_tmpl_ids" in fields_list:
            res["product_tmpl_ids"] = [(6, 0, self.env.context.get("active_ids", []))]
        return res

    def action_apply(self):
        _check_manager(self.env)
        self.ensure_one()
        if self.action == "discount":
            return self._apply_discount()
        if not self.warehouse_id:
            raise UserError(_("Choose the warehouse the stock leaves from."))
        if self.action == "transfer":
            if not self.dest_warehouse_id or self.dest_warehouse_id == self.warehouse_id:
                raise UserError(_("Choose another warehouse to move the stock to."))
            return self._create_picking(self.warehouse_id.int_type_id, self.dest_warehouse_id.lot_stock_id)
        vendor = self.vendor_id
        if not vendor:
            raise UserError(_("Choose the vendor to return the stock to."))
        return self._create_picking(self.warehouse_id.out_type_id, vendor.property_stock_supplier, vendor)

    def _apply_discount(self):
        if not self.pricelist_id or not 0 < self.discount <= 100:
            raise UserError(_("Choose a pricelist and a discount between 0 and 100%."))
        Item = self.env["product.pricelist.item"]
        for tmpl in self.product_tmpl_ids:
            Item.create({
                "pricelist_id": self.pricelist_id.id,
                "applied_on": "1_product",
                "product_tmpl_id": tmpl.id,
                "compute_price": "percentage",
                "percent_price": self.discount,
            })
            tmpl.message_post(body=_("Dead stock: %(pct)s%% discount on pricelist %(pl)s.",
                                     pct=self.discount, pl=self.pricelist_id.display_name))
        return {"type": "ir.actions.act_window_close"}

    def _create_picking(self, picking_type, dest_location, partner=None):
        if not picking_type:
            raise UserError(_("Warehouse %s has no operation type for this.", self.warehouse_id.name))
        source = self.warehouse_id.lot_stock_id
        moves = []
        for tmpl in self.product_tmpl_ids:
            for variant in tmpl.product_variant_ids:
                qty = variant.with_context(warehouse_id=self.warehouse_id.id).qty_available
                if qty > 0:
                    moves.append((0, 0, {
                        "product_id": variant.id, "product_uom_qty": qty, "product_uom": variant.uom_id.id,
                        "location_id": source.id, "location_dest_id": dest_location.id,
                    }))
        if not moves:
            raise UserError(_("There is no stock of these products in %s.", self.warehouse_id.name))
        picking = self.env["stock.picking"].create({
            "picking_type_id": picking_type.id,
            "location_id": source.id,
            "location_dest_id": dest_location.id,
            "partner_id": partner.id if partner else False,
            "origin": _("Dead stock"),
            "move_ids": moves,
        })
        picking.action_confirm()
        return {"type": "ir.actions.act_window", "res_model": "stock.picking", "res_id": picking.id,
                "views": [[False, "form"]]}
