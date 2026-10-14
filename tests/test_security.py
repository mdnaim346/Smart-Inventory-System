from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged

from .common import SmartInventoryCase


@tagged("post_install", "-at_install")
class TestSecurity(SmartInventoryCase):
    """E5.3"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        base_groups = "base.group_user,stock.group_stock_user,purchase.group_purchase_user,sales_team.group_sale_salesman"
        cls.si_user = new_test_user(cls.env, "si_user", groups=base_groups + ",smart_inventory.group_smart_inventory_user")
        cls.si_manager = new_test_user(
            cls.env, "si_manager",
            groups=base_groups + ",stock.group_stock_manager,smart_inventory.group_smart_inventory_manager")
        cls.product = cls.make_product("SI Secure")

    def test_user_cannot_restock_or_change_settings(self):
        product = self.product.with_user(self.si_user)
        with self.assertRaises(AccessError):
            product.action_check_and_restock()
        with self.assertRaises(AccessError):
            product.action_generate_ai_insights()
        with self.assertRaises(AccessError):
            product.low_stock_threshold = 1
        # but can read forecasts and dashboard data
        self.env["smart.inventory.dashboard"].with_user(self.si_user).get_dashboard_data(30, {})
        product.forecast_ids.read(["predicted"])

    def test_manager_can(self):
        product = self.product.with_user(self.si_manager)
        product.low_stock_threshold = 20
        product.action_check_and_restock()
        self.assertTrue(self.smart_pos(self.product))

    def test_user_cannot_approve(self):
        self.set_param("po_approval_amount", "1")
        self.product.action_check_and_restock()
        po = self.smart_pos(self.product)
        with self.assertRaises(AccessError):
            po.with_user(self.si_user).action_si_approve()

    def test_product_access_not_granted_to_everyone(self):
        access = self.env["ir.model.access"].search([
            ("model_id.model", "=", "product.template"), ("group_id", "=", False),
            ("perm_write", "=", True)])
        self.assertFalse(access, "No global write access on products")
