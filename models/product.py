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

    # =========================
    # AI ENHANCEMENTS
    # =========================
    predicted_demand = fields.Float(
        string="AI Predicted Demand (7 Days)",
        help="Next 7 days sales prediction powered by AI"
    )

    ai_insight = fields.Text(
        string="AI Business Insight",
        help="Strategic advice based on sales trends and stock levels"
    )

    risk_level = fields.Selection([
        ('low', 'Low Risk'),
        ('medium', 'Medium Risk'),
        ('high', 'High Risk (Stockout Likely)')
    ], string="AI Risk Assessment", default='low')

    last_ai_update = fields.Datetime(string="Last AI Update")

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

            # Update AI Insights before making a decision
            product.action_generate_ai_insights()

            # AI-Driven Decision: If predicted demand > available OR AI recommends reorder
            should_reorder = False
            if product.predicted_demand > product.qty_available:
                should_reorder = True
            elif product.risk_level == 'high':
                should_reorder = True
            
            if not should_reorder:
                continue

            vendor = product._get_vendor()
            if not vendor:
                continue

            po = PurchaseOrder.create({
                "partner_id": vendor.id,
                "origin": "AI Smart Restock",
                "order_line": [(0, 0, {
                    "product_id": product.product_variant_id.id,
                    "product_qty": max(product.restock_quantity, product.predicted_demand),
                    "price_unit": product.standard_price,
                    "name": product.name,
                    "date_planned": fields.Datetime.now(),
                })]
            })

            # Notify user (activity)
            product._create_notification_activity(po)

        return True

    def action_generate_ai_insights(self):
        """
        Gathers product data and calls AI to generate demand prediction and business advice.
        """
        for rec in self:
            # 1. Gather Sales Data (last 30 days)
            thirty_days_ago = fields.Datetime.subtract(fields.Datetime.now(), days=30)
            sales_lines = self.env['sale.order.line'].search([
                ('product_id', '=', rec.product_variant_id.id),
                ('state', 'in', ['sale', 'done']),
                ('create_date', '>=', thirty_days_ago)
            ])
            total_sales = sum(sales_lines.mapped('product_uom_qty'))
            avg_daily = total_sales / 30

            # 2. Construct Prompt
            prompt = f"""
            Product: {rec.name}
            Current Stock: {rec.qty_available}
            Sales last 30 days: {total_sales} units
            Daily Average: {avg_daily:.2f}
            
            Task:
            1. Predict demand for next 7 days.
            2. Suggest risk level (low, medium, high).
            3. Give a 1-sentence business insight.
            Format: PREDICTION:X, RISK:Y, INSIGHT:Z
            """

            # 3. Call AI Service (Mock for now, can be connected to OpenAI/Anthropic)
            ai_response = rec._ask_ai(prompt)
            
            # Parsing mock response (in real app, use Regex or JSON response)
            rec.predicted_demand = avg_daily * 7 * 1.2 # Scenario: AI predicts 20% growth
            if rec.qty_available < rec.predicted_demand:
                rec.risk_level = 'high'
                rec.ai_insight = f"Demand spike expected. Current stock of {rec.qty_available} will run out in {int(rec.qty_available/avg_daily) if avg_daily > 0 else 0} days."
            else:
                rec.risk_level = 'low'
                rec.ai_insight = "Stock levels are healthy for the current sales trend."
            
            rec.last_ai_update = fields.Datetime.now()

    def _ask_ai(self, prompt):
        """
        Utility method to interact with AI APIs.
        Users can replace this with a real OpenAI/Anthropic call.
        """
        # Placeholder for real AI API call
        # Example: return openai.ChatCompletion.create(...)
        return "PREDICTION:15, RISK:high, INSIGHT: Stock for Product A is likely to run out."

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