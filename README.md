# Smart Inventory (`smart_inventory`)

Odoo 19 addon for demand forecasting, smart replenishment and stock health.
Install the folder as `smart_inventory` on the addons path.

## What it does

**Forecasting**
- Daily cron computes a statistical baseline per product and warehouse: weighted
  7/30/90-day average of confirmed sales (by order date) with a day-of-week factor.
- Optional Claude AI adjustment (see *AI setup* below). Claude returns JSON
  (`prediction`, `risk`, `insight`) through structured outputs; the reply is validated
  and the prediction is kept within *Max AI Adjustment (%)* of the baseline. On a missing
  package, API error, refusal or invalid reply the baseline is kept. Batch size and a
  daily call budget are configurable.
- Only products with new sales or stock moves are refreshed; every run is stored in
  *Reporting → Forecast History* and compared with actual sales (MAPE).

**Replenishment**
- Per product: *Manual threshold* (Low Stock Alert / Restock Quantity) or *Computed
  reorder point* = demand during vendor lead time + safety stock (service level, by
  ABC class).
- Order quantity covers the target days of cover, rounded up to the vendor's minimum
  quantity and *Pack Multiple* (vendor price list).
- Vendor: preferred vendor, else the cheapest valid vendor price line.
- One draft "Smart Restock" PO per vendor and warehouse; open RFQs and incoming
  receipts count as stock, so reruns do not duplicate.
- Before buying, a transfer is suggested from another warehouse with excess stock.
- Restock POs above an amount need a *Smart Inventory Manager*'s approval.
- Alternative mode: only maintain Odoo reordering rules (no POs).

**Stock health**
- Low-stock flags always use the live quantity (expired lots excluded when
  `product_expiry` is installed).
- Dead stock (no sale for N days) is flagged and never restocked.
- Monthly ABC (sales value) and XYZ (demand variability) classes.

**Dashboard and reports**
- KPIs: stock value, turnover, days of cover, fill rate, stockouts; filters by period,
  category, warehouse, vendor and risk (remembered in the browser).
- Forecast chart per product (sales, forecast, stock), expiring lots, approvals and
  transfer suggestions.
- XLSX and PDF exports: shortages, forecast, dead stock.
- Daily e-mail digest of high-risk products and pending restock POs.

**Sales automation** (Settings → Smart Inventory, per company)
- Block out-of-stock sales, auto-validate deliveries (with or without backorders),
  auto-post invoices. Each can be turned off.

## Security

| Group | Can |
| --- | --- |
| Smart Inventory / User | See dashboard, forecasts, stock levels |
| Smart Inventory / Manager | Change restock settings, run restock and forecasts, approve POs, create transfers, Settings |

## Install

1. Put this folder on the addons path as `smart_inventory`.
2. `pip install xlsxwriter` (required) and `pip install anthropic` (only for AI).
3. Install *AI-Powered Smart Inventory* from Apps. Install `product_expiry` too if you
   track lot expiry dates.
4. A database created with demo data gets four sample products, two vendors and 60 days
   of sales, so the dashboard, forecast chart and restock flow can be tried at once.

## AI setup

1. *Smart Inventory → Configuration → Settings → Forecasting*, turn on *Use AI Provider*.
2. Enter a Claude API key (administrators only), or leave it empty and set
   `ANTHROPIC_API_KEY` in the server environment.
3. *Claude Model* defaults to `claude-opus-5-5`. Requests use low effort, structured
   JSON output and, on models that support it, server-side refusal fallback.
4. Set *Max AI Calls per Day* to control cost. Only products whose sales or stock
   changed are sent, and only product name and quantities leave the database.

## Tests

```bash
odoo-bin -c <config> -d <test_db> -i smart_inventory,product_expiry \
  --test-tags /smart_inventory --stop-after-init
```

CI (`.github/workflows/ci.yml`) runs `ruff` and the test suite on Odoo 19 for every PR.
