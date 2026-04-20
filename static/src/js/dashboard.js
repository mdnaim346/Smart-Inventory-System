/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";

export class InventoryDashboard extends Component {
    setup() {
        this.totalProducts = 0;
        this.lowStock = 0;

        this.loadData();
    }

    async loadData() {
        const data = await this.env.services.rpc({
            model: "product.template",
            method: "search_read",
            args: [[], ["name", "qty_available"]],
        });

        this.totalProducts = data.length;
        this.lowStock = data.filter(p => p.qty_available < 10).length;

        this.render();
    }
}

InventoryDashboard.template = "smart_inventory.Dashboard";

registry.category("actions").add("smart_inventory_dashboard", InventoryDashboard);