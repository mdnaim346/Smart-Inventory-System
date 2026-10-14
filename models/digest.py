from markupsafe import Markup

from odoo import api, fields, models, _


class SmartInventoryDigest(models.AbstractModel):
    """Daily e-mail with high-risk products and pending smart restock POs (E4.4)."""
    _name = "smart.inventory.digest"
    _description = "Smart Inventory Daily Digest"

    @api.model
    def _recipients(self, company):
        users = company.si_digest_user_ids
        if not users:
            users = self.env.ref("smart_inventory.group_smart_inventory_manager").all_user_ids.filtered(
                lambda u: company in u.company_ids)
        return users.filtered(lambda u: u.active and not u.share and u.email)

    @api.model
    def _digest_content(self, company):
        products = self.env["product.template"].with_company(company).search([
            ("is_storable", "=", True), ("risk_level", "=", "high"),
            ("company_id", "in", [False, company.id])])
        orders = self.env["purchase.order"].search(
            self.env["purchase.order"]._si_smart_domain()
            + [("state", "in", ("draft", "sent")), ("company_id", "=", company.id)])
        return products, orders

    @api.model
    def _render(self, company, products, orders):
        return self.env["ir.qweb"]._render("smart_inventory.digest_email", {
            "company": company,
            "products": products,
            "orders": orders,
            "date": fields.Date.context_today(self),
        })

    @api.model
    def _cron_send_digest(self):
        mails = self.env["mail.mail"]
        for company in self.env["res.company"].search([]):
            products, orders = self._digest_content(company)
            if not products and not orders:
                continue
            recipients = self._recipients(company)
            if not recipients:
                continue
            mails |= mails.sudo().create({
                "subject": _("Smart Inventory digest: %(risks)s high-risk products, %(pos)s pending POs",
                             risks=len(products), pos=len(orders)),
                "body_html": Markup(self._render(company, products, orders)),
                "email_from": company.email_formatted or self.env.user.email_formatted,
                "recipient_ids": [(6, 0, recipients.partner_id.ids)],
                "auto_delete": True,
            })
        return mails
