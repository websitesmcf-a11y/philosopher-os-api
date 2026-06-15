"""Prometheus metrics for Socrates AI."""

from prometheus_client import Counter, Histogram, Gauge

# HTTP request metrics
http_requests_total = Counter(
    "socrates_http_requests_total",
    "Total HTTP requests",
    labelnames=["method", "endpoint", "status"],
)

http_request_duration = Histogram(
    "socrates_http_request_duration_seconds",
    "HTTP request duration in seconds",
    labelnames=["method", "endpoint"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# Database metrics
db_query_duration = Histogram(
    "socrates_db_query_duration_seconds",
    "Database query duration in seconds",
    labelnames=["operation", "table"],
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0),
)

# Agent metrics
agent_actions_total = Counter(
    "socrates_agent_actions_total",
    "Total agent actions",
    labelnames=["agent_name", "success"],
)

agent_tasks_in_progress = Gauge(
    "socrates_agent_tasks_in_progress",
    "Number of agent tasks currently in progress",
    labelnames=["agent_name"],
)

# Campaign metrics
campaign_sends_total = Counter(
    "socrates_campaign_sends_total",
    "Total campaign messages sent",
    labelnames=["campaign_id", "channel"],
)

# Business metrics
active_campaigns = Gauge(
    "socrates_active_campaigns",
    "Number of active campaigns",
)

total_mrr = Gauge(
    "socrates_total_mrr",
    "Total Monthly Recurring Revenue in cents",
)
