---
name: smart-inventory-roadmap
description: Feature roadmap for the smart_inventory Odoo 19 addon (mdnaim346/Smart-Inventory-System) — 5 epics (E1–E5) with serialized sub-issues E1.1…E5.5 on GitHub. Use when planning, adding, picking up or updating roadmap features, or when asked what to build next in this repo.
---

# Smart Inventory Roadmap

The roadmap lives in `features.json` (source of truth) and on GitHub as epics with native sub-issues.
Board: "Smart Inventory Board" (user project #2, config in `.github/project-config.json`).

## Serial scheme

- Epics: `[Epic] E<n> <title>` — E1, E2, …
- Children: `[<Type>] E<n>.<m> <title>` — e.g. `[Feature] E1.2 …`, `[Bug] E5.1 …`
- Serial order inside an epic = suggested build order. Next free serial = last number + 1.
- Child bodies start with `**Parent:** #<epic>`; epics keep a `## Sub-Issues` checklist.

## Epics (created 2026-10-13)

| Epic | Issue | Children | Points | Why |
| --- | --- | --- | --- | --- |
| E1 Real AI Demand Forecasting | #4 | #5–#9 | 13 | `_ask_ai` is mocked; demand is `avg*7*1.2` |
| E2 Smart Replenishment Engine | #10 | #11–#16 | 15 | fixed threshold, first vendor, one PO per product, two restock code paths |
| E3 Multi-Warehouse and Stock Health | #17 | #18–#22 | 15 | company-wide qty only, no expiry/dead stock |
| E4 Dashboard, Alerts and Reports | #23 | #24–#28 | 12 | dashboard has no KPIs, charts, filters, alerts |
| E5 Configurable Sales Flow, Security and Quality | #29 | #30–#34 | 12 | hard-coded auto invoice/delivery, Odoo 19 `is_storable` bug, no groups/CI |

`created.json` maps serial → issue number/URL after each run.

## Status

- 2026-10-14: everything except **E1.1 (#5, real LLM provider)** implemented on branch
  `feat/roadmap-non-ai`, together with bug #1. E1.1 only needs `product.template._ask_ai(prompt)`
  to return the provider's text reply; parsing, budget, batching and fallback already exist.
- Module map: forecasting `models/forecast_engine.py` + `models/forecast.py`; restock/stock health
  `models/product.py`; per-warehouse levels and transfers `models/stock_level.py`; approval
  `models/purchase_order.py`; sale automation `models/sale_order.py`; dashboard/exports
  `models/dashboard.py`, `controllers/export.py`; digest `models/digest.py`.
- Tests: `tests/` (68 tests incl. a Chrome smoke test `test_ui.py`), run with
  `--test-tags /smart_inventory`. Local test DB used so far: `smart_inv_test`
  (addons path symlinks this repo as `smart_inventory`).

## Suggested order

E5.1 (P0 bug) → E5.2 → E1.1–E1.3 → E2.4 → E2.1 → E5.3/E5.4 → rest by priority.
Pre-existing bugs #1 (stale stock flags) and #2 are outside the roadmap but block E4.

## How to add or change features

1. Edit `features.json` (append to an epic's `features`, or add a new epic `E6`). Never renumber existing items.
2. From the repo root: `python3 .claude/skills/smart-inventory-roadmap/create_issues.py --dry-run`, then without `--dry-run`.
   - Idempotent: issues whose title already starts with `[Type] <serial> ` are reused, sub-issue links re-applied, epic checklist refreshed.
   - Issues/links use REST; board fields (Status=Todo, Priority, Size, Estimate) use GraphQL at the end. If GraphQL is rate-limited the script says so — just rerun later.
   - `gh project item-list` can lag right after adds, so the "already on board" check may skip items. Verify with a direct GraphQL query on `projectV2.items`; `item-add` is idempotent, so re-adding is safe.
3. Sizes: XS=1, S=2, M=3, L=5, XL=8 points.

## Working on a feature

- Branch `feat/<issue>-<slug>` (e.g. `feat/30-is-storable-fix`), PR body `Closes #<issue>`.
- Tests: `odoo-bin -c <config> -d <test_db> -u smart_inventory --test-enable --stop-after-init`.
- Odoo 19: storable = `product.is_storable`; `detailed_type` and `type == 'product'` are gone.
