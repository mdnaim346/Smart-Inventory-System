# Changelog

## 19.0.3.2.0

- Restock suggestions inbox: optional review step where a manager approves, edits or
  rejects each need, with a plain-language reason. A rejection silences the same
  product and warehouse for 7 days.
- Weekly restock budget: highest risks are ordered first; needs above the budget
  become suggestions.
- Seasons and promotions (demand factor per day, by product or category). Past event
  days are normalised so festivals do not inflate normal demand.
- New products can forecast like a similar product until they have 30 days of sales.
- Vendor scorecard: on-time receipts, quantity delivered, delay, price change, score.
- Overstock flag and menu; dashboard money row: sales lost to stockouts, cash tied up,
  money saved by transfers, weekly budget left or forecast error.
- Lost sales log (daily stockout record), Forecast Accuracy report.
- Dead stock actions: pricelist discount, return to vendor, move to another warehouse.
- ABC cycle counting, stock adjustment reasons (optionally required) and a report by
  reason, First Expired First Out removal for products with expiration dates.
- Telegram and webhook alerts (for a WhatsApp or SMS gateway): new high-risk products
  and the daily summary.
- Dashboard: readable "Predictive Optimization" heading, phone and tablet layout.
- Roadmap criteria: AI calls log product, tokens and latency (never the key); a failed
  forecast batch no longer undoes earlier batches; optional economic order quantity;
  reordering rules follow the computed reorder point on every forecast run; dashboard
  filters also apply to restock order cards; no digest e-mail on days without high-risk
  products; CI measures coverage of models (at least 70%).

## 19.0.3.1.0

- Claude AI provider for forecasts (issue #5, E1.1): API key, model and maximum
  adjustment in Settings; structured JSON output; server-side refusal fallback;
  baseline kept on missing package, error, refusal or cut-off reply.
- AI forecast is capped to a configurable percentage of the statistical baseline.
- Demo data: four products, two vendors, stock and 60 days of sales.
- Apps Store page, app icon and banner; module is now an application.
- Backend no longer loads Google Fonts from the internet.
- Dashboard button renamed from "RESTOCK SHIELD" to "Run Restock".

## 19.0.3.0.0

- Forecasting, smart replenishment, multi-warehouse stock levels, stock health,
  dashboard, reports, digest, configurable sales automation, security groups,
  tests and CI (roadmap epics E1–E5 except E1.1).
