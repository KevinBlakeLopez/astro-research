"""GeoPosition validation.

Swiss Ephemeris accepts out-of-range coordinates without complaint (observed: latitude 95
returns an "Ascendant", longitude 32768 silently wraps to 8E), so the engine validates them.
"""

import math

import pytest

from astro_research.geo import GeoPosition, InvalidCoordinates


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(90.0, 0.0), (-90.0, 0.0), (0.0, 180.0), (0.0, -180.0), (51.5, -0.1278)],
)
def test_accepts_coordinates_in_range(latitude: float, longitude: float) -> None:
    place = GeoPosition(latitude=latitude, longitude=longitude)
    assert (place.latitude, place.longitude, place.elevation_m) == (latitude, longitude, 0.0)


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        (95.0, 0.0),
        (-90.0001, 0.0),
        (0.0, 180.0001),
        (0.0, 32768.0),
        (math.nan, 0.0),
        (0.0, math.inf),
    ],
)
def test_rejects_coordinates_out_of_range(latitude: float, longitude: float) -> None:
    with pytest.raises(InvalidCoordinates):
        GeoPosition(latitude=latitude, longitude=longitude)


def test_rejects_non_finite_elevation() -> None:
    with pytest.raises(InvalidCoordinates):
        GeoPosition(latitude=0.0, longitude=0.0, elevation_m=math.nan)
