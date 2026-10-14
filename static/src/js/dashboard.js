/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { browser } from "@web/core/browser/browser";

const FILTER_KEY = "smart_inventory.dashboard.filters";
const EMPTY_FILTERS = { categ_id: "", warehouse_id: "", vendor_id: "", risk_level: "" };

export class InventoryDashboard extends Component {
    static template = "smart_inventory.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        const saved = this.loadSavedFilters();
        this.state = useState({
            loading: true,
            periodDays: saved.periodDays || 30,
            filters: { ...EMPTY_FILTERS, ...(saved.filters || {}) },
            options: { categories: [], warehouses: [], vendors: [], risk_levels: [] },
            kpis: {},
            totalProducts: 0,
            lowStockCount: 0,
            pendingOrders: 0,
            deadStockCount: 0,
            lowStockProducts: [],
            aiRecommendations: [],
            recentActivities: [],
            pendingApprovals: [],
            expiringLots: [],
            transferSuggestions: [],
            isManager: false,
        });

        onWillStart(async () => {
            this.state.options = await this.orm.call("smart.inventory.dashboard", "get_filter_options", []);
            this.dropStaleFilters();
            await this.loadData();
        });
    }

    // ------------------------------------------------------------------
    // Filters (persisted in the browser, E4.3)
    // ------------------------------------------------------------------
    loadSavedFilters() {
        try {
            return JSON.parse(browser.localStorage.getItem(FILTER_KEY) || "{}");
        } catch {
            return {};
        }
    }

    /** Saved ids can point to records that no longer exist or belong to another company. */
    dropStaleFilters() {
        const lists = {
            categ_id: "categories",
            warehouse_id: "warehouses",
            vendor_id: "vendors",
            risk_level: "risk_levels",
        };
        for (const [key, list] of Object.entries(lists)) {
            const value = this.state.filters[key];
            if (value && !this.state.options[list].some((o) => String(o.id) === String(value))) {
                this.state.filters[key] = "";
            }
        }
    }

    get activeFilters() {
        const result = {};
        for (const [key, value] of Object.entries(this.state.filters)) {
            if (value) {
                result[key] = value;
            }
        }
        return result;
    }

    async onFilterChange(key, ev) {
        this.state.filters[key] = ev.target.value;
        await this.saveAndReload();
    }

    async onPeriodChange(ev) {
        this.state.periodDays = parseInt(ev.target.value) || 30;
        await this.saveAndReload();
    }

    async clearFilters() {
        this.state.filters = { ...EMPTY_FILTERS };
        await this.saveAndReload();
    }

    async saveAndReload() {
        browser.localStorage.setItem(
            FILTER_KEY,
            JSON.stringify({ filters: this.state.filters, periodDays: this.state.periodDays })
        );
        await this.loadData();
    }

    // ------------------------------------------------------------------
    // Data
    // ------------------------------------------------------------------
    async loadData() {
        this.state.loading = true;
        try {
            const data = await this.orm.call("smart.inventory.dashboard", "get_dashboard_data", [
                this.state.periodDays,
                this.activeFilters,
            ]);
            Object.assign(this.state, data);
        } catch (error) {
            console.error("Dashboard failed to load:", error);
            this.notification.add("Smart Inventory dashboard failed to load.", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    stockPercent(product) {
        if (!product.low_stock_threshold) {
            return 0;
        }
        return Math.max(0, Math.min(100, (product.qty_available / product.low_stock_threshold) * 100));
    }

    formatNumber(value) {
        return (value || 0).toLocaleString(undefined, { maximumFractionDigits: 2 });
    }

    // ------------------------------------------------------------------
    // Navigation and actions
    // ------------------------------------------------------------------
    openRecord(model, resId) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            res_id: resId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openProduct(productId) {
        this.openRecord("product.template", productId);
    }

    openPurchaseOrder(poId) {
        this.openRecord("purchase.order", poId);
    }

    openChart(productId) {
        this.action.doAction({
            type: "ir.actions.client",
            tag: "smart_inventory_forecast_chart",
            params: { product_tmpl_id: productId },
        });
    }

    async triggerRestock() {
        await this.orm.call("product.template", "action_check_and_restock", [[]]);
        this.notification.add("Restock run finished.", { type: "success" });
        await this.loadData();
    }

    async approvePurchaseOrder(poId) {
        await this.orm.call("purchase.order", "action_si_approve", [[poId]]);
        await this.loadData();
    }

    async rejectPurchaseOrder(poId) {
        const action = await this.orm.call("purchase.order", "action_si_reject", [[poId]]);
        await this.action.doAction(action, { onClose: () => this.loadData() });
    }

    async createTransfer(suggestionId) {
        await this.orm.call("smart.inventory.transfer.suggestion", "action_create_transfer", [[suggestionId]]);
        await this.loadData();
    }

    exportXlsx(kind) {
        const filters = encodeURIComponent(JSON.stringify(this.activeFilters));
        browser.open(`/smart_inventory/export/${kind}.xlsx?filters=${filters}`, "_blank");
    }

    async exportPdf(kind) {
        await this.action.doAction({
            type: "ir.actions.report",
            report_type: "qweb-pdf",
            report_name: "smart_inventory.report_stock_list",
            report_file: "smart_inventory.report_stock_list",
            data: { kind, filters: this.activeFilters },
        });
    }
}

registry.category("actions").add("smart_inventory_dashboard", InventoryDashboard);
