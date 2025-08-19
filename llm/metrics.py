"""
metrics.py — продвинутый метрик-коллектор для SoVAni (Prometheus + multi-tenancy + security)
"""
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from prometheus_client import CollectorRegistry
from aiohttp import web
import asyncio
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
import os
import re

def sanitize_label(label: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_]', '_', label)[:32]

class MetricsProvider(ABC):
    @abstractmethod
    async def record_success(self, tenant_id: str, provider: str, model: str, usage: dict, cost: float, latency_s: float): ...
    @abstractmethod
    async def record_error(self, tenant_id: str, provider: str, model: str, error_type: str): ...
    @abstractmethod
    async def record_token_usage(self, tenant_id: str, provider: str, model: str, prompt_tokens: int, completion_tokens: int): ...

class PrometheusMetricsProvider(MetricsProvider):
    def __init__(self, registry: Optional[CollectorRegistry] = None):
        self.registry = registry or CollectorRegistry()
        self.success = Counter(
            "llm_success_total",
            "LLM успешные вызовы по tenant",
            ["tenant_id", "provider", "model"], registry=self.registry
        )
        self.error = Counter(
            "llm_error_total",
            "Ошибки LLM по tenant",
            ["tenant_id", "provider", "model", "error_type"], registry=self.registry
        )
        self.latency = Histogram(
            "llm_latency_seconds",
            "Задержка LLM (сек)", ["tenant_id", "provider", "model"], registry=self.registry
        )
        self.cost = Counter(
            "llm_cost_usd_total",
            "LLM стоимость (USD)", ["tenant_id", "provider", "model"], registry=self.registry
        )
        self.token_usage = Counter(
            "llm_token_usage_total",
            "LLM токены по типу", ["tenant_id", "provider", "model", "token_type"], registry=self.registry
        )
        self.concurrent = Gauge(
            "llm_concurrent_requests",
            "LLM concurrent requests", ["tenant_id", "provider", "model"], registry=self.registry
        )

    async def record_success(self, tenant_id, provider, model, usage, cost, latency_s):
        tenant_id, provider, model = map(sanitize_label, (tenant_id, provider, model))
        self.success.labels(tenant_id, provider, model).inc()
        self.latency.labels(tenant_id, provider, model).observe(latency_s)
        self.cost.labels(tenant_id, provider, model).inc(cost)
        self.concurrent.labels(tenant_id, provider, model).dec()

    async def record_error(self, tenant_id, provider, model, error_type):
        tenant_id, provider, model, error_type = map(sanitize_label, (tenant_id, provider, model, error_type))
        self.error.labels(tenant_id, provider, model, error_type).inc()
        self.concurrent.labels(tenant_id, provider, model).dec()

    async def record_token_usage(self, tenant_id, provider, model, prompt_tokens, completion_tokens):
        tenant_id, provider, model = map(sanitize_label, (tenant_id, provider, model))
        self.token_usage.labels(tenant_id, provider, model, "prompt").inc(prompt_tokens)
        self.token_usage.labels(tenant_id, provider, model, "completion").inc(completion_tokens)
        self.concurrent.labels(tenant_id, provider, model).inc()

# HTTP endpoint с аутентификацией
async def metrics_handler(request):
    api_key = os.getenv('METRICS_AUTH_KEY')
    if api_key and request.headers.get('X-Metrics-Auth') != api_key:
        raise web.HTTPForbidden()
    provider: PrometheusMetricsProvider = request.app["metrics"]
    resp = web.Response(body=generate_latest(provider.registry))
    resp.content_type = CONTENT_TYPE_LATEST
    return resp

def run_metrics_server(metrics_provider, port: int = 8000):
    app = web.Application()
    app["metrics"] = metrics_provider
    app.router.add_get('/metrics', metrics_handler)
    web.run_app(app, port=port)

# Для тестирования и DI можно реализовать MockMetricsProvider и BufferedMetricsProvider

