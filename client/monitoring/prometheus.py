"""Prometheus metrics exporter for CORE-CLIENT."""

from prometheus_client import Counter, Histogram, Gauge, generate_latest
from fastapi import Response

# Connection metrics
core_client_connections_active = Gauge(
    "core_client_connections_active",
    "Number of active connections",
)
core_client_connections_total = Counter(
    "core_client_connections_total",
    "Total connections established",
)
core_client_reconnects_total = Counter(
    "core_client_reconnects_total",
    "Total reconnections",
)

# Request metrics
core_client_requests_total = Counter(
    "core_client_requests_total",
    "Total requests sent",
    ["method", "status"],
)
core_client_request_latency_seconds = Histogram(
    "core_client_request_latency_seconds",
    "Request latency",
    ["method"],
)

# Response metrics
core_client_responses_total = Counter(
    "core_client_responses_total",
    "Total responses received",
    ["message_type", "status"],
)

# Unsolicited queue metrics
core_client_unsolicited_queue_size = Gauge(
    "core_client_unsolicited_queue_size",
    "Size of unsolicited message queue",
)
core_client_unsolicited_total = Counter(
    "core_client_unsolicited_total",
    "Total unsolicited messages received",
    ["message_type"],
)

# TLS metrics
core_client_tls_handshake_duration_seconds = Histogram(
    "core_client_tls_handshake_duration_seconds",
    "TLS handshake duration",
)
core_client_tls_failures_total = Counter(
    "core_client_tls_failures_total",
    "Total TLS failures",
)

# Session metrics
core_client_session_duration_seconds = Histogram(
    "core_client_session_duration_seconds",
    "Session duration",
)
core_client_lease_remaining_seconds = Gauge(
    "core_client_lease_remaining_seconds",
    "Lease remaining seconds",
)

# Device metrics
core_client_device_messages_sent_total = Counter(
    "core_client_device_messages_sent_total",
    "Total device messages sent",
    ["destination", "message_type", "status"],
)
core_client_device_messages_received_total = Counter(
    "core_client_device_messages_received_total",
    "Total device messages received",
    ["source", "message_type"],
)


def metrics_endpoint() -> Response:
    """FastAPI endpoint for Prometheus metrics."""
    return Response(
        content=generate_latest(),
        media_type="application/openmetrics-text; version=1.0.0; charset=utf-8",
    )