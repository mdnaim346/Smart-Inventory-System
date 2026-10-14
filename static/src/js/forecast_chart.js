/** @odoo-module **/

import { Component, onMounted, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/** Actual sales, stock level and forecast over time for one product (E4.2). */
export class ForecastChart extends Component {
    static template = "smart_inventory.ForecastChart";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.canvasRef = useRef("canvas");
        this.productId = this.props.action?.params?.product_tmpl_id;
        this.state = useState({ data: null });

        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            if (this.productId) {
                this.state.data = await this.orm.call("smart.inventory.dashboard", "get_product_chart", [
                    this.productId,
                ]);
            }
        });
        onMounted(() => this.renderChart());
        onWillUnmount(() => this.chart?.destroy());
    }

    cssVar(name, fallback) {
        const value = getComputedStyle(document.body).getPropertyValue(name).trim();
        return value || fallback;
    }

    renderChart() {
        const data = this.state.data;
        if (!data || !data.labels || !this.canvasRef.el) {
            return;
        }
        const text = this.cssVar("--body-color", "#495057");
        const grid = this.cssVar("--border-color", "rgba(128,128,128,0.2)");
        this.chart = new Chart(this.canvasRef.el, {
            type: "line",
            data: {
                labels: data.labels,
                datasets: [
                    {
                        label: "Actual sales",
                        data: data.sales,
                        borderColor: "#2a7ab0",
                        backgroundColor: "rgba(42,122,176,0.15)",
                        pointRadius: 0,
                        tension: 0.2,
                        yAxisID: "y",
                    },
                    {
                        label: "Forecast (per day)",
                        data: data.forecast,
                        borderColor: "#e8833a",
                        borderDash: [6, 4],
                        pointRadius: 0,
                        tension: 0.2,
                        yAxisID: "y",
                    },
                    {
                        label: "Stock on hand",
                        data: data.stock,
                        borderColor: "#3a9d5d",
                        pointRadius: 0,
                        stepped: true,
                        yAxisID: "y1",
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: "index", intersect: false },
                plugins: { legend: { labels: { color: text } } },
                scales: {
                    x: { ticks: { color: text, maxTicksLimit: 12 }, grid: { color: grid } },
                    y: {
                        position: "left",
                        beginAtZero: true,
                        title: { display: true, text: "Units / day", color: text },
                        ticks: { color: text },
                        grid: { color: grid },
                    },
                    y1: {
                        position: "right",
                        beginAtZero: true,
                        title: { display: true, text: "Stock", color: text },
                        ticks: { color: text },
                        grid: { drawOnChartArea: false },
                    },
                },
            },
        });
    }

    openProduct() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "product.template",
            res_id: this.productId,
            views: [[false, "form"]],
        });
    }
}

registry.category("actions").add("smart_inventory_forecast_chart", ForecastChart);
