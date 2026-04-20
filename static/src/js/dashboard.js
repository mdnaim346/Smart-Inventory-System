/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class InventoryDashboard extends Component {
    setup() {
        this.orm = useService("orm");
        this.state = useState({
            totalProducts: 0,
            lowStock: 0,
        });

        onWillStart(async () => {
            await this.loadData();
        });
    }

    async loadData() {
        try {
            const data = await this.orm.searchRead(
                "product.template",
                [],
                ["name", "qty_available"]
            );

            this.state.totalProducts = data.length || 0;
            this.state.lowStock = data.filter(p => p.qty_available < 10).length || 0;
        } catch (error) {
            console.error("Dashboard failed to load:", error);
        }
    }
}

InventoryDashboard.template = "smart_inventory.Dashboard";
registry.category("actions").add("smart_inventory_dashboard", InventoryDashboard);