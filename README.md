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

**Purchasing control**
- *Review Suggestions Before Ordering*: restock needs wait in *Replenishment → Suggestions
  to Review* with the reason ("stock position 18 against restock level 40..."). A manager
  approves (the line goes to the vendor's draft PO), edits quantity, vendor or price, or
  rejects with a reason (no new suggestion for that product and warehouse for 7 days).
- *Weekly Restock Budget*: the restock run orders the highest risks first; needs that do
  not fit this week's budget become suggestions marked *over budget*.
- *Vendor Scorecard* (Reporting): on-time receipts, quantity delivered, average delay and
  price change over 180 days, combined into a score. Refreshed weekly.

**Demand planning**
- *Seasons and Promotions* (Configuration): a demand factor for a date range, for all
  products, some categories or some products. Forecast days are multiplied by it; sales on
  past event days are divided by it.
- *Forecast Like* on a product: a new product uses a similar product's sales history until
  it has 30 days of its own.

**Stock money and counting**
- Overstock (more days of cover than the limit in Settings) and its excess value.
- Dashboard money row: sales lost to stockouts, cash tied up in overstock and dead stock,
  money saved by transfers, weekly budget left (or forecast error).
- *Clear Dead Stock* action on products: pricelist discount, return to vendor, or move to
  another warehouse.
- *Cycle Count*: next count dates by ABC class (A every 30 days, B 90, C 180 by default) in
  Odoo's Physical Inventory; search by barcode.
- Adjustment reasons on counts (optionally required) and *Reporting → Stock Adjustments*
  by reason and month.
- *First Expired, First Out* for products with expiration dates (needs Product Expiry).

**Chat alerts**
- Telegram bot (token and chat ID) and/or a webhook URL that receives `{"text": ...}`,
  for a WhatsApp or SMS gateway: new high-risk products and the daily summary.

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
