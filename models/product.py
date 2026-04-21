from odoo import models, fields, api, _
from odoo.exceptions import UserError


class ProductTemplate(models.Model):
    _inherit = "product.template"

    # =========================
    # CONFIG FIELDS
    # =========================
    low_stock_threshold = fields.Integer(
        string="Low Stock Alert",
        default=10,
        help="Minimum quantity before triggering restock"
    )

    auto_restock = fields.Boolean(
        string="Enable Auto Restock",
        default=True
    )

    restock_quantity = fields.Integer(
        string="Restock Quantity",
        default=50
    )

    preferred_vendor_id = fields.Many2one(
        "res.partner",
        string="Preferred Vendor",
        domain=[("supplier_rank", ">", 0)]
    )

    # =========================
    # COMPUTED FIELDS
    # =========================
    is_low_stock = fields.Boolean(
        compute="_compute_stock_status",
        store=True
    )

    forecast_shortage = fields.Float(
        string="Forecast Shortage",
        compute="_compute_stock_status",
        store=True
    )

    @api.depends("qty_available", "low_stock_threshold")
    def _compute_stock_status(self):
        for rec in self:
            rec.is_low_stock = rec.qty_available <= rec.low_stock_threshold
            rec.forecast_shortage = max(
                0, rec.low_stock_threshold - rec.qty_available
            )

    # =========================
    # BUSINESS LOGIC
    # =========================
    def action_check_and_restock(self):
        """
        Main function to:
        - Detect low stock
        - Create Purchase Order
        - Notify responsible user
        """
        PurchaseOrder = self.env["purchase.order"]

        for product in self:
            if not product.auto_restock:
                continue

            if not product.is_low_stock:
                continue

            vendor = product._get_vendor()

            if not vendor:
                raise UserError(
                    _("No vendor found for product: %s") % product.name
                )

            po = PurchaseOrder.create({
                "partner_id": vendor.id,
                "origin": "Auto Restock",
                "order_line": [(0, 0, {
                    "product_id": product.product_variant_id.id,
                    "product_qty": product.restock_quantity,
                    "price_unit": product.standard_price,
                    "name": product.name,
                    "date_planned": fields.Datetime.now(),
                })]
            })

            # Notify user (activity)
            product._create_notification_activity(po)

        return True

    # =========================
    # HELPER METHODS
    # =========================
    def _get_vendor(self):
        """
        Smart vendor selection:
        1. Preferred vendor
        2. Supplier list
        """
        self.ensure_one()

        if self.preferred_vendor_id:
            return self.preferred_vendor_id

        if self.seller_ids:
            return self.seller_ids[0].partner_id

        return False

    def _create_notification_activity(self, purchase_order):
        """
        Create activity for admin/user
        """
        activity_type = self.env.ref("mail.mail_activity_data_todo")

        self.env["mail.activity"].create({
            "res_model_id": self.env["ir.model"]._get_id("purchase.order"),
            "res_id": purchase_order.id,
            "activity_type_id": activity_type.id,
            "summary": "Auto Purchase Created",
            "note": "PO created automatically for low stock",
            "user_id": self.env.user.id,
        })


class ProductProduct(models.Model):
    _inherit = "product.product"

    def _get_vendor(self):
        return self.product_tmpl_id._get_vendor()

    def _create_notification_activity(self, purchase_order):
        return self.product_tmpl_id._create_notification_activity(purchase_order)

    def action_check_and_restock(self):
        return self.product_tmpl_id.action_check_and_restock()