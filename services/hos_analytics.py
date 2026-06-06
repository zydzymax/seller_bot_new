"""
Head of Sales analytics service (self-hosted).

- Event ingestion into PostgreSQL
- KPI computation for dashboard/managers/funnel/forecast
"""

from __future__ import annotations

import os
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import asyncpg


POSTGRES_DSN = os.getenv("POSTGRES_DSN", "")
_pool: Optional[asyncpg.Pool] = None
_schema_ready = False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _pct_change(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0 if current == 0 else 100.0
    return ((current - previous) / previous) * 100.0


def _rating(conversion: float, deals_count: int) -> tuple[str, str]:
    if deals_count >= 10 and conversion >= 30:
        return "A+", "excellent"
    if deals_count >= 7 and conversion >= 20:
        return "A", "good"
    if deals_count >= 4 and conversion >= 12:
        return "B+", "average"
    if deals_count >= 2 and conversion >= 8:
        return "B", "average"
    return "C", "risk"


def _safe_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _stage_label(stage: str) -> str:
    mapping = {
        "new": "Новые",
        "qualification": "Квалификация",
        "presentation": "Презентация",
        "proposal": "КП отправлено",
        "negotiation": "Переговоры",
        "won": "Закрыто успешно",
        "lost": "Закрыто неуспешно",
    }
    return mapping.get(stage, stage)


def _stage_color(stage: str) -> str:
    mapping = {
        "new": "#4A90D9",
        "qualification": "#7B68EE",
        "presentation": "#9370DB",
        "proposal": "#BA55D3",
        "negotiation": "#DA70D6",
        "won": "#32CD32",
        "lost": "#E57373",
    }
    return mapping.get(stage, "#4A90D9")


class HOSAnalyticsService:
    def __init__(self, dsn: str):
        self.dsn = dsn

    async def _get_pool(self) -> asyncpg.Pool:
        global _pool
        if _pool is None:
            if not self.dsn:
                raise RuntimeError("POSTGRES_DSN is not configured")
            _pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=10)
        return _pool

    async def _ensure_schema(self) -> None:
        global _schema_ready
        if _schema_ready:
            return

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS hos_events (
                    id BIGSERIAL PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    lead_id TEXT,
                    deal_id TEXT,
                    manager_id TEXT,
                    stage TEXT,
                    amount NUMERIC(14,2),
                    duration_seconds INTEGER,
                    sentiment DOUBLE PRECISION,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE INDEX IF NOT EXISTS idx_hos_events_account_time
                ON hos_events(account_id, occurred_at DESC);

                CREATE INDEX IF NOT EXISTS idx_hos_events_account_type
                ON hos_events(account_id, event_type);

                CREATE INDEX IF NOT EXISTS idx_hos_events_account_deal
                ON hos_events(account_id, deal_id);

                CREATE INDEX IF NOT EXISTS idx_hos_events_account_manager
                ON hos_events(account_id, manager_id);
                """
            )
        _schema_ready = True

    async def ingest_events(self, account_id: str, events: List[Dict[str, Any]]) -> int:
        await self._ensure_schema()
        pool = await self._get_pool()

        rows = []
        now = _utc_now()
        for event in events:
            occurred_at_raw = event.get("occurred_at")
            occurred_at = now
            if occurred_at_raw:
                try:
                    occurred_at = datetime.fromisoformat(
                        str(occurred_at_raw).replace("Z", "+00:00")
                    )
                    if occurred_at.tzinfo is None:
                        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
                except ValueError:
                    occurred_at = now

            rows.append(
                (
                    account_id,
                    str(event.get("event_type") or "unknown"),
                    occurred_at,
                    str(event.get("lead_id") or "") or None,
                    str(event.get("deal_id") or "") or None,
                    str(event.get("manager_id") or "") or None,
                    str(event.get("stage") or "") or None,
                    _safe_float(event.get("amount")) or None,
                    _safe_int(event.get("duration_seconds")) or None,
                    _safe_float(event.get("sentiment")) if event.get("sentiment") is not None else None,
                    event.get("payload") if isinstance(event.get("payload"), dict) else {},
                )
            )

        if not rows:
            return 0

        async with pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO hos_events (
                    account_id, event_type, occurred_at, lead_id, deal_id, manager_id,
                    stage, amount, duration_seconds, sentiment, payload
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb)
                """,
                rows,
            )
        return len(rows)

    async def _period_stats(self, account_id: str, start: datetime) -> Dict[str, Any]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    COUNT(DISTINCT deal_id) FILTER (
                        WHERE deal_id IS NOT NULL
                          AND event_type IN ('deal_created', 'deal_stage_changed', 'deal_won', 'deal_lost')
                    ) AS total_deals,
                    COUNT(DISTINCT deal_id) FILTER (
                        WHERE deal_id IS NOT NULL AND event_type = 'deal_won'
                    ) AS won_deals,
                    COUNT(DISTINCT deal_id) FILTER (
                        WHERE deal_id IS NOT NULL AND event_type = 'deal_lost'
                    ) AS lost_deals,
                    COUNT(DISTINCT lead_id) FILTER (
                        WHERE lead_id IS NOT NULL
                          AND event_type IN ('lead_created', 'deal_created', 'deal_stage_changed', 'deal_won', 'deal_lost')
                    ) AS leads_count,
                    COALESCE(SUM(amount) FILTER (WHERE event_type = 'deal_won'), 0) AS revenue
                FROM hos_events
                WHERE account_id = $1
                  AND occurred_at >= $2
                """,
                account_id,
                start,
            )
        return dict(row or {})

    async def _avg_deal_days(self, account_id: str, start: datetime) -> float:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                WITH created AS (
                    SELECT deal_id, MIN(occurred_at) AS created_at
                    FROM hos_events
                    WHERE account_id = $1
                      AND deal_id IS NOT NULL
                      AND event_type IN ('lead_created', 'deal_created')
                    GROUP BY deal_id
                ),
                won AS (
                    SELECT deal_id, MIN(occurred_at) AS won_at
                    FROM hos_events
                    WHERE account_id = $1
                      AND deal_id IS NOT NULL
                      AND event_type = 'deal_won'
                      AND occurred_at >= $2
                    GROUP BY deal_id
                )
                SELECT COALESCE(
                    AVG(EXTRACT(EPOCH FROM (won.won_at - created.created_at)) / 86400.0),
                    0
                ) AS avg_days
                FROM won
                JOIN created USING (deal_id)
                WHERE won.won_at >= created.created_at
                """,
                account_id,
                start,
            )
        return _safe_float(row["avg_days"] if row else 0)

    async def get_managers(self, account_id: str, period_days: int) -> List[Dict[str, Any]]:
        await self._ensure_schema()
        start = _utc_now() - timedelta(days=period_days)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    COALESCE(NULLIF(manager_id, ''), 'unknown') AS manager_id,
                    COUNT(DISTINCT deal_id) FILTER (
                        WHERE deal_id IS NOT NULL
                          AND event_type IN ('deal_created', 'deal_stage_changed', 'deal_won', 'deal_lost')
                    ) AS deals_count,
                    COUNT(DISTINCT deal_id) FILTER (
                        WHERE deal_id IS NOT NULL AND event_type = 'deal_won'
                    ) AS won_deals,
                    COALESCE(SUM(amount) FILTER (WHERE event_type = 'deal_won'), 0) AS revenue
                FROM hos_events
                WHERE account_id = $1
                  AND occurred_at >= $2
                GROUP BY COALESCE(NULLIF(manager_id, ''), 'unknown')
                ORDER BY revenue DESC, deals_count DESC
                LIMIT 20
                """,
                account_id,
                start,
            )

        result: List[Dict[str, Any]] = []
        idx = 1
        for row in rows:
            deals_count = _safe_int(row["deals_count"])
            won_deals = _safe_int(row["won_deals"])
            conversion = round((won_deals / deals_count) * 100.0, 2) if deals_count else 0.0
            rating, rating_class = _rating(conversion, deals_count)
            result.append(
                {
                    "id": idx,
                    "name": row["manager_id"],
                    "avatar": "",
                    "deals_count": deals_count,
                    "revenue": _safe_float(row["revenue"]),
                    "conversion": conversion,
                    "rating": rating,
                    "rating_class": rating_class,
                }
            )
            idx += 1
        return result

    async def get_funnel(self, account_id: str, period_days: int) -> List[Dict[str, Any]]:
        await self._ensure_schema()
        start = _utc_now() - timedelta(days=period_days)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH latest_stage AS (
                    SELECT DISTINCT ON (deal_id)
                        deal_id,
                        COALESCE(NULLIF(stage, ''), 'new') AS stage
                    FROM hos_events
                    WHERE account_id = $1
                      AND occurred_at >= $2
                      AND deal_id IS NOT NULL
                    ORDER BY deal_id, occurred_at DESC
                )
                SELECT stage, COUNT(*) AS count
                FROM latest_stage
                GROUP BY stage
                ORDER BY count DESC
                """,
                account_id,
                start,
            )

        if not rows:
            return []

        total_top = max(_safe_int(r["count"]) for r in rows) or 1
        result: List[Dict[str, Any]] = []
        for row in rows:
            stage = str(row["stage"])
            count = _safe_int(row["count"])
            result.append(
                {
                    "name": _stage_label(stage),
                    "stage": stage,
                    "count": count,
                    "width": max(8, int((count / total_top) * 100)),
                    "color": _stage_color(stage),
                    "conversion": round((count / total_top) * 100, 2),
                }
            )
        return result

    async def get_forecast(self, account_id: str, period_days: int) -> Dict[str, Any]:
        await self._ensure_schema()
        start = _utc_now() - timedelta(days=period_days)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    DATE_TRUNC('day', occurred_at) AS day,
                    COUNT(DISTINCT deal_id) FILTER (
                        WHERE deal_id IS NOT NULL AND event_type = 'deal_won'
                    ) AS won_deals,
                    COALESCE(SUM(amount) FILTER (WHERE event_type = 'deal_won'), 0) AS revenue
                FROM hos_events
                WHERE account_id = $1
                  AND occurred_at >= $2
                GROUP BY DATE_TRUNC('day', occurred_at)
                ORDER BY day
                """,
                account_id,
                start,
            )

        if not rows:
            return {
                "forecast_revenue": 0.0,
                "forecast_deals": 0,
                "confidence": 20,
                "factors": [{"factor": "Недостаточно данных", "impact": "0%"}],
                "recommendations": [
                    "Начните отправлять события в /api/widget/hos/events/ingest"
                ],
            }

        revenues = [_safe_float(r["revenue"]) for r in rows]
        won_deals = [_safe_int(r["won_deals"]) for r in rows]
        avg_daily_revenue = sum(revenues) / len(revenues)
        avg_daily_deals = sum(won_deals) / len(won_deals)

        revenue_forecast = round(avg_daily_revenue * period_days, 2)
        deals_forecast = int(round(avg_daily_deals * period_days))

        avg = avg_daily_revenue if avg_daily_revenue else 1.0
        volatility = statistics.pstdev(revenues) / avg if len(revenues) > 1 else 0.0

        confidence = 45
        confidence += min(30, len(rows))
        confidence -= min(30, int(volatility * 100))
        confidence = max(20, min(95, confidence))

        return {
            "forecast_revenue": revenue_forecast,
            "forecast_deals": deals_forecast,
            "confidence": confidence,
            "factors": [
                {"factor": "Средний дневной темп выручки", "impact": f"{avg_daily_revenue:,.0f} ₽/день"},
                {"factor": "Волатильность закрытий", "impact": f"{volatility * 100:.1f}%"},
            ],
            "recommendations": [
                "Увеличить долю сделок, переходящих в стадию переговоров",
                "Сократить время реакции на новые лиды",
            ],
        }

    async def get_dashboard(self, account_id: str, period_days: int) -> Dict[str, Any]:
        await self._ensure_schema()
        now = _utc_now()
        start_current = now - timedelta(days=period_days)
        start_prev = now - timedelta(days=period_days * 2)

        current = await self._period_stats(account_id, start_current)
        previous = await self._period_stats(account_id, start_prev)

        total_deals = _safe_int(current.get("total_deals"))
        won_deals = _safe_int(current.get("won_deals"))
        leads_count = _safe_int(current.get("leads_count"))
        lost_deals = _safe_int(current.get("lost_deals"))
        total_revenue = _safe_float(current.get("revenue"))

        # Если нет deal_id событий, используем лиды как базовую оценку потока.
        if total_deals == 0 and leads_count > 0:
            total_deals = leads_count

        base_for_conversion = leads_count or total_deals
        conversion_rate = round((won_deals / base_for_conversion) * 100.0, 2) if base_for_conversion else 0.0

        prev_total_deals = _safe_int(previous.get("total_deals"))
        prev_revenue = _safe_float(previous.get("revenue"))
        prev_won = _safe_int(previous.get("won_deals"))
        prev_leads = _safe_int(previous.get("leads_count"))
        prev_base = prev_leads or prev_total_deals
        prev_conversion = round((prev_won / prev_base) * 100.0, 2) if prev_base else 0.0

        deals_change = round(_pct_change(total_deals, prev_total_deals), 2)
        revenue_change = round(_pct_change(total_revenue, prev_revenue), 2)
        conversion_change = round(conversion_rate - prev_conversion, 2)

        avg_deal_days = await self._avg_deal_days(account_id, start_current)
        avg_deal_time = f"{max(0, int(round(avg_deal_days)))} дней"

        managers = await self.get_managers(account_id, period_days)
        funnel_stages = await self.get_funnel(account_id, period_days)
        forecast = await self.get_forecast(account_id, period_days)

        insights: List[Dict[str, Any]] = []
        if total_deals == 0 and leads_count == 0:
            insights.append(
                {
                    "type": "info",
                    "icon": "ℹ️",
                    "text": "Данных недостаточно. Отправляйте события в ingest endpoint.",
                }
            )
        if lost_deals > won_deals and lost_deals >= 3:
            insights.append(
                {
                    "type": "warning",
                    "icon": "⚠️",
                    "text": "Потерянных сделок больше, чем выигранных. Проверьте этапы квалификации и возражения по цене.",
                }
            )
        if conversion_change > 0:
            insights.append(
                {
                    "type": "success",
                    "icon": "📈",
                    "text": f"Конверсия выросла на {conversion_change:.1f} п.п. к прошлому периоду.",
                }
            )
        if managers:
            top_manager = managers[0]
            insights.append(
                {
                    "type": "info",
                    "icon": "🏆",
                    "text": (
                        f"Лидер периода: {top_manager['name']} "
                        f"(выручка {top_manager['revenue']:,.0f} ₽)."
                    ),
                }
            )

        return {
            "total_deals": total_deals,
            "total_revenue": total_revenue,
            "conversion_rate": conversion_rate,
            "avg_deal_time": avg_deal_time,
            "deals_change": deals_change,
            "revenue_change": revenue_change,
            "conversion_change": conversion_change,
            "cycle_change": 0.0,
            "managers": managers,
            "funnel_stages": funnel_stages,
            "insights": insights,
            "forecast_revenue": _safe_float(forecast.get("forecast_revenue")),
            "forecast_deals": _safe_int(forecast.get("forecast_deals")),
            "forecast_confidence": _safe_int(forecast.get("confidence")),
        }


_service: Optional[HOSAnalyticsService] = None


async def get_hos_analytics_service() -> HOSAnalyticsService:
    global _service
    if _service is None:
        _service = HOSAnalyticsService(POSTGRES_DSN)
    return _service

