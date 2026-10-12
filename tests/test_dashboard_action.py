from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDashboardAction(TransactionCase):

    def test_dashboard_action_name(self):
        """The dashboard title shown in the breadcrumb must be spelled correctly
        and contain no stray whitespace or newlines (issue #2)."""
        action = self.env.ref("smart_inventory.smart_inventory_dashboard_action")
        self.assertEqual(action.name, "Inventory Dashboard")
