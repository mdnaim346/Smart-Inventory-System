import math
from collections import defaultdict
from datetime import datetime, time, timedelta
from statistics import NormalDist, pstdev
from zoneinfo import ZoneInfo

from odoo import api, fields, models

# Weights of the 7/30/90-day daily averages in the baseline (E1.3).
WINDOW_WEIGHTS = ((7, 0.5), (30, 0.3), (90, 0.2))


class SmartInventoryForecastEngine(models.AbstractModel):
    """Deterministic demand statistics shared by forecasting and replenishment."""
    _name = "smart.inventory.forecast.engine"
    _description = "Smart Inventory Forecast Engine"

    # ------------------------------------------------------------------
    # Demand history
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Business days: one timezone for "today", filters and day grouping
    # ------------------------------------------------------------------
    @api.model
    def _tz(self):
        return self.env.context.get("tz") or self.env.user.tz or "UTC"

    @api.model
    def _today(self):
        return datetime.now(ZoneInfo(self._tz())).date()

    @api.model
    def _day_start_utc(self, day):
        """Naive UTC datetime of local midnight starting `day`."""
        local = datetime.combine(day, time.min, tzinfo=ZoneInfo(self._tz()))
        return local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

    @api.model
    def _local_date(self, utc_dt):
        return utc_dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(self._tz())).date()

    @api.model
    def _daily_sales(self, products, days, warehouse=None, end=None):
        """Return {product_id: {date: qty}} of confirmed sales (by order date)
        over the last `days` local days up to `end` (exclusive, default: today)."""
        end = end or self._today()
        start = end - timedelta(days=days)
        domain = [
            ("product_id", "in", products.ids),
            ("state", "=", "sale"),
            ("date", ">=", self._day_start_utc(start)),
            ("date", "<", self._day_start_utc(end)),
        ]
        if warehouse:
            domain.append(("warehouse_id", "=", warehouse.id))
        groups = self._sale_report_groups(domain, ["product_id", "date:day"], ["product_uom_qty:sum"])
        result = defaultdict(dict)
        for product, day, qty in groups:
            result[product.id][fields.Date.to_date(day)] = qty
        return result

    @api.model
    def _sale_report_groups(self, domain, groupby, aggregates):
        """Read `sale.report` for the active companies, grouping days in `_tz()`.
        It is a SQL view, so pending ORM writes must reach the database first."""
        self.env.flush_all()
        domain = [*domain, ("company_id", "in", self.env.companies.ids)]
        return self.env["sale.report"].sudo().with_context(tz=self._tz())._read_group(
            domain, groupby, aggregates)

    @staticmethod
    def _series(sales_by_day, days, end):
        """Dense list of daily quantities, oldest first."""
        return [sales_by_day.get(end - timedelta(days=days - i), 0.0) for i in range(days)]

    # ------------------------------------------------------------------
    # Baseline forecast (E1.3)
    # ------------------------------------------------------------------
    @api.model
    def _baseline(self, sales_by_day, horizon, end=None):
        """Weighted moving average with a day-of-week factor.

        :param sales_by_day: {date: qty} for at least the last 90 days
        :return: dict(forecast=total over horizon, daily=avg daily demand,
                      sigma=std dev of daily demand (30 days))
        """
        end = end or self._today()
        series90 = self._series(sales_by_day, 90, end)
        daily = sum(w * (sum(series90[-n:]) / n) for n, w in WINDOW_WEIGHTS)
        sigma = pstdev(series90[-30:]) if any(series90[-30:]) else 0.0

        # Day-of-week factor from the 90-day window (1.0 when there is no history).
        by_dow = defaultdict(list)
        for i, qty in enumerate(series90):
            by_dow[(end - timedelta(days=90 - i)).weekday()].append(qty)
        mean = sum(series90) / 90
        factors = {
            dow: ((sum(v) / len(v)) / mean if mean else 1.0) for dow, v in by_dow.items()
        }
        forecast = sum(daily * factors.get((end + timedelta(days=d)).weekday(), 1.0)
                       for d in range(horizon))
        return {"forecast": forecast, "daily": daily, "sigma": sigma}

    # ------------------------------------------------------------------
    # Safety stock and reorder point (E2.1)
    # ------------------------------------------------------------------
    @staticmethod
    def _z(service_level_pct):
        level = min(max(service_level_pct / 100.0, 0.5), 0.9999)
        return NormalDist().inv_cdf(level)

    @api.model
    def _safety_stock(self, sigma, lead_time_days, service_level_pct):
        return self._z(service_level_pct) * sigma * math.sqrt(max(lead_time_days, 1))

    @api.model
    def _reorder_point(self, daily, sigma, lead_time_days, service_level_pct):
        safety = self._safety_stock(sigma, lead_time_days, service_level_pct)
        return daily * max(lead_time_days, 1) + safety, safety

    # ------------------------------------------------------------------
    # Accuracy (E1.4)
    # ------------------------------------------------------------------
    @staticmethod
    def _mape(pairs):
        """Mean absolute percentage error (in %) over (predicted, actual) pairs
        with a non-zero actual. Returns 0.0 when there is nothing to compare."""
        errors = [abs(p - a) / a for p, a in pairs if a]
        return 100.0 * sum(errors) / len(errors) if errors else 0.0

    # ------------------------------------------------------------------
    # Classification (E3.4)
    # ------------------------------------------------------------------
    @staticmethod
    def _abc(values):
        """{key: value} -> {key: 'a'|'b'|'c'} by cumulative share 80/15/5."""
        total = sum(values.values())
        result, running = {}, 0.0
        for key, value in sorted(values.items(), key=lambda kv: kv[1], reverse=True):
            if not total or value <= 0:
                result[key] = "c"
                continue
            share_before = running / total
            running += value
            result[key] = "a" if share_before < 0.80 else "b" if share_before < 0.95 else "c"
        return result

    @staticmethod
    def _xyz(weekly):
        """Coefficient of variation of weekly demand -> 'x' (<0.5), 'y' (<1.0), 'z'."""
        mean = sum(weekly) / len(weekly) if weekly else 0.0
        if not mean:
            return "z"
        cv = pstdev(weekly) / mean
        return "x" if cv < 0.5 else "y" if cv < 1.0 else "z"
