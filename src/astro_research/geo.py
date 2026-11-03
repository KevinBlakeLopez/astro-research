"""Geographic position of a birthplace."""

import math
from dataclasses import dataclass


class InvalidCoordinates(ValueError):
    """A coordinate is outside its valid range or is not a finite number."""


@dataclass(frozen=True)
class GeoPosition:
    """Geodetic position in degrees.

    Longitude is east-positive, the Swiss Ephemeris convention (Astrolog uses west-positive).
    Swiss Ephemeris does not validate coordinates, so they are validated here.
    """

    latitude: float
    longitude: float
    elevation_m: float = 0.0

    def __post_init__(self) -> None:
        if not (math.isfinite(self.latitude) and -90.0 <= self.latitude <= 90.0):
            raise InvalidCoordinates(f"latitude {self.latitude!r} is not within [-90, 90]")
        if not (math.isfinite(self.longitude) and -180.0 <= self.longitude <= 180.0):
            raise InvalidCoordinates(f"longitude {self.longitude!r} is not within [-180, 180]")
        if not math.isfinite(self.elevation_m):
            raise InvalidCoordinates(f"elevation {self.elevation_m!r} is not a finite number")
