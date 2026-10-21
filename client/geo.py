"""Optional device location model + Haversine distance (stdlib only).

Coordinates are never fabricated: a location exists only when supplied
from a trustworthy source (device report, manual configuration, or
opt-in public-IP geolocation done elsewhere). Otherwise everything
reports Unknown. Private LAN IPs carry no geographic meaning and are
never used for location or distance.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

SOURCE_DEVICE_REPORTED = "device_reported"
SOURCE_MANUAL = "manual"
SOURCE_GEOIP = "geoip"
SOURCE_UNKNOWN = "unknown"

PRECISION_EXACT = "exact"
PRECISION_APPROXIMATE = "approximate"
PRECISION_CITY = "city"
PRECISION_HIDDEN = "hidden"

EARTH_RADIUS_M = 6371000.0


def utcnow_iso() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def make_location(
    *,
    latitude: float | None,
    longitude: float | None,
    source: str = SOURCE_UNKNOWN,
    accuracy_meters: float | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build a location record; invalid coordinates become Unknown."""
    if (
        not isinstance(latitude, (int, float))
        or not isinstance(longitude, (int, float))
        or isinstance(latitude, bool)
        or isinstance(longitude, bool)
        or not (-90.0 <= float(latitude) <= 90.0)
        or not (-180.0 <= float(longitude) <= 180.0)
    ):
        return {
            "latitude": None,
            "longitude": None,
            "source": SOURCE_UNKNOWN,
            "accuracy_meters": None,
            "timestamp": timestamp or utcnow_iso(),
        }
    return {
        "latitude": float(latitude),
        "longitude": float(longitude),
        "source": source,
        "accuracy_meters": accuracy_meters,
        "timestamp": timestamp or utcnow_iso(),
    }


def unknown_location() -> dict[str, Any]:
    """Explicitly unknown location."""
    return make_location(latitude=None, longitude=None)


def has_coordinates(location: Mapping | None) -> bool:
    """Whether a location record carries usable coordinates."""
    if not isinstance(location, dict):
        return False
    latitude = location.get("latitude")
    longitude = location.get("longitude")
    return isinstance(latitude, (int, float)) and isinstance(
        longitude, (int, float)
    )


def apply_precision(
    location: dict[str, Any], precision: str = PRECISION_APPROXIMATE
) -> dict[str, Any]:
    """Reduce coordinate precision for privacy before sharing."""
    if not has_coordinates(location):
        return dict(location)
    if precision == PRECISION_HIDDEN:
        redacted = dict(location)
        redacted["latitude"] = None
        redacted["longitude"] = None
        redacted["source"] = SOURCE_UNKNOWN
        return redacted
    if precision == PRECISION_EXACT:
        return dict(location)
    latitude = float(location["latitude"])
    longitude = float(location["longitude"])
    if precision == PRECISION_CITY:
        decimals = 1
    else:  # approximate (default)
        decimals = 2
    rounded = dict(location)
    rounded["latitude"] = round(latitude, decimals)
    rounded["longitude"] = round(longitude, decimals)
    return rounded


def haversine_meters(
    latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float
) -> float:
    """Great-circle distance in meters between two coordinates."""
    phi_a = math.radians(latitude_a)
    phi_b = math.radians(latitude_b)
    delta_phi = math.radians(latitude_b - latitude_a)
    delta_lambda = math.radians(longitude_b - longitude_a)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def distance_between(first: dict | None, second: dict | None) -> float | None:
    """Distance in meters, or None when either side lacks coordinates."""
    if not has_coordinates(first) or not has_coordinates(second):
        return None
    return haversine_meters(
        float(first["latitude"]),
        float(first["longitude"]),
        float(second["latitude"]),
        float(second["longitude"]),
    )


def format_distance(meters: float | None) -> str:
    """Human display: '~2.4 km away', '~350 m away', or unavailable."""
    if meters is None or meters < 0:
        return "Distance unavailable"
    if meters < 1000:
        return f"~{meters:.0f} m away"
    return f"~{meters / 1000:.1f} km away"


def format_location(location: dict | None) -> str:
    """Human display for a location record."""
    if not has_coordinates(location):
        return "Location: Unknown"
    assert location is not None
    return (
        f"{location['latitude']:.4f}, {location['longitude']:.4f} "
        f"({location.get('source', SOURCE_UNKNOWN)})"
    )
