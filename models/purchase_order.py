from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .product import LEGACY_ORIGINS, SMART_ORIGIN, _check_manager

MANAGER_GROUP = "smart_inventory.group_smart_inventory_manager"


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    si_is_smart_restock = fields.Boolean(string="Smart Restock", readonly=True, index=True)
    si_approval_state = fields.Selection(
        [("not_required", "Not Required"), ("pending", "Pending Approval"),
         ("approved", "Approved"), ("rejected", "Rejected")],
        string="Restock Approval", default="not_required", readonly=True, copy=False, tracking=True)
    si_reject_reason = fields.Text(string="Rejection Reason", readonly=True, copy=False)
    si_approved_by_id = fields.Many2one("res.users", string="Approved By", readonly=True, copy=False)
    si_approved_amount = fields.Float(
        string="Approved Amount", readonly=True, copy=False, help="Untaxed amount in company currency when approved.")

    @api.model
    def _si_smart_domain(self):
        return ["|", ("si_is_smart_restock", "=", True), ("origin", "in", [SMART_ORIGIN, *LEGACY_ORIGINS])]

    # ------------------------------------------------------------------
    # Consolidation (E2.4)
    # ------------------------------------------------------------------
    @api.model
    def _si_add_restock_line(self, vendor, warehouse, product, qty):
        """Add `qty` of `product` to the vendor's draft smart PO for `warehouse`,
        creating the PO if needed. Returns the PO."""
        order = self.search([
            ("si_is_smart_restock", "=", True),
            ("state", "=", "draft"),
            ("partner_id", "=", vendor.id),
            ("company_id", "=", warehouse.company_id.id),
            ("picking_type_id", "=", warehouse.in_type_id.id),
            ("si_approval_state", "!=", "rejected"),
        ], limit=1)
        if not order:
            order = self.create({
                "partner_id": vendor.id,
                "origin": SMART_ORIGIN,
                "si_is_smart_restock": True,
                "company_id": warehouse.company_id.id,
                "picking_type_id": warehouse.in_type_id.id,
            })
            order._si_notify_created()
        line = order.order_line.filtered(lambda line: line.product_id == product)[:1]
        if line:
            line.product_qty += product.uom_id._compute_quantity(qty, line.product_uom_id)
        else:
            self.env["purchase.order.line"].create({
                "order_id": order.id,
                "product_id": product.id,
                "product_qty": qty,
                "product_uom_id": product.uom_id.id,
            })
        return order

    def _si_notify_created(self):
        activity_type = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        for order in self:
            order.activity_schedule(
                activity_type_id=activity_type.id if activity_type else False,
                summary=_("Auto Purchase Created"),
                note=_("PO created automatically by Smart Inventory for low stock."),
                user_id=self.env.user.id,
            )

    # ------------------------------------------------------------------
    # Approval (E2.5)
    # ------------------------------------------------------------------
    def _si_amount_company(self):
        """Untaxed amount in company currency (the approval threshold's currency)."""
        self.ensure_one()
        return self.currency_id._convert(
            self.amount_untaxed, self.company_id.currency_id, self.company_id,
            self.date_order or fields.Date.context_today(self))

    def _si_check_approval(self):
        """(Re)decide whether smart restock POs need approval. Also run on confirmation,
        so increasing an approved or small PO afterwards cannot skip the approval."""
        threshold = self.env["smart.inventory.config"].get("po_approval_amount")
        for order in self.filtered(lambda o: o.si_is_smart_restock and o.state in ("draft", "sent")):
            above = bool(threshold) and order._si_amount_company() > threshold
            state = order.si_approval_state
            if state == "rejected":
                continue
            if state == "approved":
                if above and order._si_amount_company() > order.si_approved_amount + 0.005:
                    order._si_request_approval()
            elif above and state != "pending":
                order._si_request_approval()
            elif not above and state == "pending":
                order.si_approval_state = "not_required"

    def _si_request_approval(self):
        group = self.env.ref(MANAGER_GROUP)
        activity_type = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        for order in self:
            order.si_approval_state = "pending"
            for user in group.all_user_ids.filtered(lambda u: not u.share and u.active)[:10]:
                order.activity_schedule(
                    activity_type_id=activity_type.id if activity_type else False,
                    summary=_("Approve smart restock PO"),
                    note=_("%(po)s (%(amount)s) is above the approval threshold.",
                           po=order.name, amount=order.amount_untaxed),
                    user_id=user.id,
                )

    def action_si_approve(self):
        _check_manager(self.env)
        for order in self.filtered(lambda o: o.si_approval_state == "pending"):
            order.write({"si_approval_state": "approved", "si_approved_by_id": self.env.uid,
                         "si_approved_amount": order._si_amount_company(), "si_reject_reason": False})
            order.activity_feedback(["mail.mail_activity_data_todo"], feedback=_("Approved"))
            order.message_post(body=_("Smart restock approved by %s.", self.env.user.name))
        return True

    def action_si_reject(self):
        _check_manager(self.env)
        return {
            "type": "ir.actions.act_window",
            "res_model": "smart.inventory.po.reject.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_order_ids": self.ids},
        }

    def _si_reject(self, reason):
        _check_manager(self.env)
        for order in self:
            order.write({"si_approval_state": "rejected", "si_reject_reason": reason})
            order.activity_feedback(["mail.mail_activity_data_todo"], feedback=_("Rejected"))
            order.message_post(body=_("Smart restock rejected by %(user)s: %(reason)s",
                                      user=self.env.user.name, reason=reason))

    def button_confirm(self):
        blocked = self.filtered(lambda o: o.si_approval_state in ("pending", "rejected"))
        if blocked:
            raise UserError(_(
                "These smart restock orders need a Smart Inventory Manager's approval first: %s",
                ", ".join(blocked.mapped("name"))))
        # Amount changed since the last decision: ask for approval instead of confirming
        # (no error, so the pending state and approval activities are kept).
        self._si_check_approval()
        newly_blocked = self.filtered(lambda o: o.si_approval_state == "pending")
        for order in newly_blocked:
            order.message_post(body=_("Amount is above the approval threshold: approval requested."))
        return super(PurchaseOrder, self - newly_blocked).button_confirm()


class SmartInventoryPoRejectWizard(models.TransientModel):
    _name = "smart.inventory.po.reject.wizard"
    _description = "Reject Smart Restock PO"

    order_ids = fields.Many2many("purchase.order", required=True)
    reason = fields.Text(required=True)

    def action_reject(self):
        self.order_ids._si_reject(self.reason)
        return {"type": "ir.actions.act_window_close"}
