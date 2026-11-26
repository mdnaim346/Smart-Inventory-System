from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .product import _check_manager

REJECT_SNOOZE_DAYS = 7


class SmartInventorySuggestion(models.Model):
    """A restock proposal a manager approves, edits or rejects before it reaches a PO."""
    _name = "smart.inventory.suggestion"
    _description = "Smart Inventory Restock Suggestion"
    _inherit = ["mail.thread"]
    _order = "state_sequence, risk_sequence, create_date desc, id desc"

    product_id = fields.Many2one("product.product", required=True, ondelete="cascade", index=True)
    product_tmpl_id = fields.Many2one(related="product_id.product_tmpl_id", store=True, index=True)
    warehouse_id = fields.Many2one("stock.warehouse", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="warehouse_id.company_id", store=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    vendor_id = fields.Many2one("res.partner", string="Vendor", required=True, tracking=True)
    suggested_qty = fields.Float(string="Suggested", digits="Product Unit", readonly=True)
    quantity = fields.Float(string="Order Quantity", digits="Product Unit", required=True, tracking=True)
    uom_id = fields.Many2one(related="product_id.uom_id")
    unit_price = fields.Float(string="Unit Price", digits="Product Price")
    amount = fields.Monetary(compute="_compute_amount", store=True)
    reason = fields.Text(string="Why", readonly=True)
    risk_level = fields.Selection(related="product_tmpl_id.risk_level", store=True)
    risk_sequence = fields.Integer(compute="_compute_sequences", store=True)
    over_budget = fields.Boolean(readonly=True, help="Created because the weekly restock budget was used up.")
    state = fields.Selection(
        [("new", "To Review"), ("approved", "Ordered"), ("rejected", "Rejected")],
        default="new", required=True, tracking=True)
    state_sequence = fields.Integer(compute="_compute_sequences", store=True)
    reject_reason = fields.Text(readonly=True)
    decided_by_id = fields.Many2one("res.users", string="Decided By", readonly=True)
    decided_date = fields.Datetime(readonly=True)
    purchase_order_id = fields.Many2one("purchase.order", string="Purchase Order", readonly=True)

    @api.depends("quantity", "unit_price")
    def _compute_amount(self):
        for rec in self:
            rec.amount = rec.quantity * rec.unit_price

    @api.depends("state", "risk_level")
    def _compute_sequences(self):
        for rec in self:
            rec.state_sequence = {"new": 0, "approved": 1, "rejected": 2}.get(rec.state, 3)
            rec.risk_sequence = {"high": 0, "medium": 1, "low": 2}.get(rec.risk_level, 3)

    # ------------------------------------------------------------------
    # Created by the restock run
    # ------------------------------------------------------------------
    @api.model
    def _si_is_snoozed(self, product, warehouse):
        """A rejection silences the same product and warehouse for a week."""
        limit = fields.Datetime.now() - timedelta(days=REJECT_SNOOZE_DAYS)
        return bool(self.search_count([
            ("product_id", "=", product.id), ("warehouse_id", "=", warehouse.id),
            ("state", "=", "rejected"), ("decided_date", ">=", limit)], limit=1))

    @api.model
    def _si_propose(self, vendor, warehouse, product, qty, seller, reason, over_budget=False):
        open_suggestion = self.search([
            ("product_id", "=", product.id), ("warehouse_id", "=", warehouse.id), ("state", "=", "new")], limit=1)
        if open_suggestion:
            open_suggestion.write({
                "suggested_qty": open_suggestion.suggested_qty + qty,
                "quantity": open_suggestion.quantity + qty,
                "reason": reason,
                "over_budget": open_suggestion.over_budget or over_budget,
            })
            return open_suggestion
        price = seller.price if seller and seller.partner_id == vendor else product.standard_price
        if seller and seller.currency_id and seller.currency_id != warehouse.company_id.currency_id:
            price = seller.currency_id._convert(price, warehouse.company_id.currency_id, warehouse.company_id,
                                                fields.Date.context_today(self))
        return self.create({
            "product_id": product.id,
            "warehouse_id": warehouse.id,
            "vendor_id": vendor.id,
            "suggested_qty": qty,
            "quantity": qty,
            "unit_price": price,
            "reason": reason,
            "over_budget": over_budget,
        })

    # ------------------------------------------------------------------
    # Manager decisions
    # ------------------------------------------------------------------
    def action_approve(self):
        _check_manager(self.env)
        todo = self.filtered(lambda s: s.state == "new")
        if any(s.quantity <= 0 for s in todo):
            raise UserError(_("Set an order quantity above zero before approving."))
        PO = self.env["purchase.order"]
        for company, group in todo.grouped("company_id").items():
            left = PO.with_company(company)._si_budget_left(company)
            total = sum(group.mapped("amount"))
            if left is not None and total > left + 0.005:
                raise UserError(_(
                    "These suggestions cost %(total).2f but only %(left).2f of this week's restock budget is left.",
                    total=total, left=max(left, 0.0)))
        orders = PO
        for rec in todo:
            order = PO.with_company(rec.company_id)._si_add_restock_line(
                rec.vendor_id, rec.warehouse_id, rec.product_id, rec.quantity)
            rec.write({"state": "approved", "purchase_order_id": order.id,
                       "decided_by_id": self.env.uid, "decided_date": fields.Datetime.now()})
            orders |= order
        orders._si_check_approval()
        return True

    def action_reject(self):
        _check_manager(self.env)
        return {
            "type": "ir.actions.act_window",
            "name": _("Reject Suggestion"),
            "res_model": "smart.inventory.suggestion.reject.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_suggestion_ids": self.filtered(lambda s: s.state == "new").ids},
        }

    def _si_reject(self, reason):
        _check_manager(self.env)
        for rec in self.filtered(lambda s: s.state == "new"):
            rec.write({"state": "rejected", "reject_reason": reason,
                       "decided_by_id": self.env.uid, "decided_date": fields.Datetime.now()})
            rec.message_post(body=_("Rejected: %s", reason))


class SmartInventorySuggestionRejectWizard(models.TransientModel):
    _name = "smart.inventory.suggestion.reject.wizard"
    _description = "Reject Restock Suggestion"

    suggestion_ids = fields.Many2many("smart.inventory.suggestion", "smart_inventory_sugg_reject_rel",
                                      "wizard_id", "suggestion_id", required=True)
    reason = fields.Text(required=True)

    def action_reject(self):
        self.suggestion_ids._si_reject(self.reason)
        return {"type": "ir.actions.act_window_close"}
