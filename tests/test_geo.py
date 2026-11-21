"""Location model + Haversine distance (offline, fixed coordinates)."""

from __future__ import annotations

from client.geo import (
    PRECISION_APPROXIMATE,
    PRECISION_CITY,
    PRECISION_EXACT,
    PRECISION_HIDDEN,
    SOURCE_MANUAL,
    apply_precision,
    distance_between,
    format_distance,
    format_location,
    has_coordinates,
    haversine_meters,
    make_location,
    unknown_location,
)

# Prompt §39 reference pair (Dubai): ~0.9 km apart by trusted calculation.
COORD_A = (25.2048, 55.2708)
COORD_B = (25.1972, 55.2744)


def test_haversine_reference_pair():
    distance = haversine_meters(*COORD_A, *COORD_B)
    assert 800.0 < distance < 1000.0


def test_haversine_same_point_is_zero():
    assert haversine_meters(*COORD_A, *COORD_A) == 0.0


def test_haversine_symmetry():
    forward = haversine_meters(*COORD_A, *COORD_B)
    backward = haversine_meters(*COORD_B, *COORD_A)
    assert forward == backward


def test_distance_between_requires_both_sides():
    location = make_location(latitude=25.2, longitude=55.2, source=SOURCE_MANUAL)
    assert distance_between(location, unknown_location()) is None
    assert distance_between(unknown_location(), location) is None
    assert distance_between(None, None) is None


def test_distance_between_valid_pair():
    first = make_location(latitude=COORD_A[0], longitude=COORD_A[1])
    second = make_location(latitude=COORD_B[0], longitude=COORD_B[1])
    distance = distance_between(first, second)
    assert distance is not None and 800.0 < distance < 1000.0


def test_format_distance_bands():
    assert format_distance(None) == "Distance unavailable"
    assert format_distance(350) == "~350 m away"
    assert format_distance(2400) == "~2.4 km away"


def test_invalid_coordinates_become_unknown():
    assert not has_coordinates(make_location(latitude=999.0, longitude=1.0))
    assert not has_coordinates(make_location(latitude="x", longitude=1.0))
    assert not has_coordinates(make_location(latitude=None, longitude=None))
    assert not has_coordinates(None)
    assert format_location(unknown_location()) == "Location: Unknown"


def test_valid_location_formatting():
    location = make_location(latitude=25.2048, longitude=55.2708, source=SOURCE_MANUAL)
    assert has_coordinates(location)
    assert "25.2048" in format_location(location)
    assert SOURCE_MANUAL in format_location(location)


def test_precision_reduction():
    location = make_location(latitude=25.2048, longitude=55.2708)
    assert apply_precision(location, PRECISION_EXACT)["latitude"] == 25.2048
    approx = apply_precision(location, PRECISION_APPROXIMATE)
    assert (approx["latitude"], approx["longitude"]) == (25.2, 55.27)
    city = apply_precision(location, PRECISION_CITY)
    assert (city["latitude"], city["longitude"]) == (25.2, 55.3)
    hidden = apply_precision(location, PRECISION_HIDDEN)
    assert not has_coordinates(hidden)


def test_precision_on_unknown_is_stable():
    redacted = apply_precision(unknown_location(), PRECISION_CITY)
    assert not has_coordinates(redacted)
