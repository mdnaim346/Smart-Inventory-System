# Smart Inventory (`smart_inventory`)

Odoo 19 addon for demand forecasting, smart replenishment and stock health.
Install the folder as `smart_inventory` on the addons path.

## What it does

**Forecasting**
- Daily cron computes a statistical baseline per product and warehouse: weighted
  7/30/90-day average of confirmed sales (by order date) with a day-of-week factor.
- Optional AI adjustment: enable *Use AI Provider* in Settings. The provider call in
  `product.template._ask_ai` is a stub until a provider is connected (issue E1.1);
  replies must be JSON (`prediction`, `risk`, `insight`) and are validated, otherwise
  the baseline is kept. Batch size and a daily call budget are configurable.
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

## Tests

```bash
odoo-bin -c <config> -d <test_db> -i smart_inventory,product_expiry \
  --test-tags /smart_inventory --stop-after-init
```

CI (`.github/workflows/ci.yml`) runs `ruff` and the test suite on Odoo 19 for every PR.
