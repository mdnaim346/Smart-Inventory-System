/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class InventoryDashboard extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            totalProducts: 0,
            lowStockCount: 0,
            pendingOrders: 0,
            lowStockProducts: [],
            aiRecommendations: [],
            recentActivities: [],
        });

        onWillStart(async () => {
            await this.loadData();
        });
    }

    async loadData() {
        try {
            // 1. Fetch Stats & Low Stock Products
            const products = await this.orm.searchRead(
                "product.template",
                [],
                ["name", "qty_available", "low_stock_threshold", "is_low_stock", "forecast_shortage", "predicted_demand", "risk_level", "ai_insight"]
            );

            this.state.totalProducts = products.length || 0;
            this.state.lowStockProducts = products.filter(p => p.is_low_stock);
            this.state.lowStockCount = this.state.lowStockProducts.length || 0;
            
            // Format numbers and filter recommendations
            this.state.aiRecommendations = products.map(p => ({
                ...p,
                predicted_demand: parseFloat(p.predicted_demand || 0).toFixed(2)
            })).filter(p => p.risk_level !== 'low' || parseFloat(p.predicted_demand) > p.qty_available).slice(0, 5);

            // 2. Fetch Recent Auto-Restock POs
            const activities = await this.orm.searchRead(
                "purchase.order",
                [["origin", "in", ["Auto Restock", "AI Smart Restock"]]],
                ["name", "partner_id", "amount_total", "state", "date_order"],
                { limit: 5, order: "date_order desc" }
            );
            this.state.recentActivities = activities;

            // 3. Fetch Pending Orders Count
            this.state.pendingOrders = await this.orm.searchCount(
                "purchase.order",
                [["state", "=", "draft"], ["origin", "in", ["Auto Restock", "AI Smart Restock"]]]
            );

        } catch (error) {
            console.error("Dashboard failed to load:", error);
        }
    }

    async openProduct(productId) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "product.template",
            res_id: productId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    async openPurchaseOrder(poId) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "purchase.order",
            res_id: poId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    async triggerRestock() {
        await this.orm.call("product.template", "action_check_and_restock", [[]]);
        await this.loadData();
    }
}

InventoryDashboard.template = "smart_inventory.Dashboard";
registry.category("actions").add("smart_inventory_dashboard", InventoryDashboard);