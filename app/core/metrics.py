"""
Every metric the app records is defined ONCE, right here, and imported
wherever it needs to be incremented. Defining the same metric name
twice in two different files is a real bug prometheus_client will
raise an error for at startup — having a single source of truth avoids
that entirely, and makes it trivial to see every metric this app
exposes just by reading this one file.
"""

from prometheus_client import Counter, Histogram

# --- HTTP-level metrics (API only) ---

http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    labelnames=["method", "route", "status_code"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    labelnames=["method", "route"],
)

# --- Business metrics (API) ---

urls_created_total = Counter(
    "urls_created_total",
    "Total short URLs created (excludes idempotent repeat-shortens of the same long URL)",
)

redirects_total = Counter(
    "redirects_total",
    "Total redirect attempts, by outcome",
    labelnames=["result"],  # "success", "not_found", "gone"
)

cache_hits_total = Counter("cache_hits_total", "Redirect cache hits")
cache_misses_total = Counter("cache_misses_total", "Redirect cache misses")

rate_limit_exceeded_total = Counter(
    "rate_limit_exceeded_total", "Requests rejected for exceeding the rate limit"
)

# --- Kafka metrics (shared: producer side used by API, consumer side used by worker) ---

click_events_published_total = Counter(
    "click_events_published_total", "Click events successfully published to Kafka"
)
click_events_publish_failed_total = Counter(
    "click_events_publish_failed_total", "Click events that failed to publish to Kafka"
)
click_events_processed_total = Counter(
    "click_events_processed_total", "Click events successfully processed by the consumer"
)
click_events_dlq_total = Counter(
    "click_events_dlq_total", "Click events routed to the dead-letter queue"
)
