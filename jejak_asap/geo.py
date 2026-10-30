"""Geometry helpers for an equirectangular (EPSG:4326) image.

A GIBS WMS image requested in EPSG:4326 maps longitude and latitude linearly
onto pixels, so conversion is a simple affine transform.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_KM = 6371.0


@dataclass(frozen=True)
class BBox:
    west: float
    south: float
    east: float
    north: float

    @classmethod
    def parse(cls, text: str) -> "BBox":
        west, south, east, north = (float(v) for v in text.split(","))
        if not (west < east and south < north):
            raise ValueError("bbox must be west,south,east,north with west<east, south<north")
        return cls(west, south, east, north)

    @property
    def lon_span(self) -> float:
        return self.east - self.west

    @property
    def lat_span(self) -> float:
        return self.north - self.south

    def contains(self, lon: float, lat: float) -> bool:
        return self.west <= lon <= self.east and self.south <= lat <= self.north

    def as_firms_area(self) -> str:
        return f"{self.west},{self.south},{self.east},{self.north}"


@dataclass(frozen=True)
class GeoImage:
    """An image whose pixel grid covers `bbox` exactly."""

    bbox: BBox
    width: int
    height: int

    def to_pixel(self, lon: float, lat: float) -> tuple[int, int]:
        x = (lon - self.bbox.west) / self.bbox.lon_span * self.width
        y = (self.bbox.north - lat) / self.bbox.lat_span * self.height
        return clamp(int(math.floor(x)), 0, self.width - 1), clamp(int(math.floor(y)), 0, self.height - 1)

    def to_lonlat(self, x: float, y: float) -> tuple[float, float]:
        lon = self.bbox.west + x / self.width * self.bbox.lon_span
        lat = self.bbox.north - y / self.height * self.bbox.lat_span
        return lon, lat

    def km_per_pixel(self) -> tuple[float, float]:
        mid_lat = math.radians((self.bbox.north + self.bbox.south) / 2)
        deg_km = math.pi * EARTH_RADIUS_KM / 180
        return (
            self.bbox.lon_span / self.width * deg_km * math.cos(mid_lat),
            self.bbox.lat_span / self.height * deg_km,
        )


def image_size_for(bbox: BBox, width: int) -> tuple[int, int]:
    """Height that keeps one pixel equally sized in degrees on both axes."""
    height = max(1, round(width * bbox.lat_span / bbox.lon_span))
    return width, height


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def destination(lon: float, lat: float, bearing_deg: float, distance_km: float) -> tuple[float, float]:
    """Point reached travelling `distance_km` along `bearing_deg` (0 = north)."""
    phi1, lam1 = math.radians(lat), math.radians(lon)
    theta = math.radians(bearing_deg)
    delta = distance_km / EARTH_RADIUS_KM
    phi2 = math.asin(math.sin(phi1) * math.cos(delta) + math.cos(phi1) * math.sin(delta) * math.cos(theta))
    lam2 = lam1 + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(phi1),
        math.cos(delta) - math.sin(phi1) * math.sin(phi2),
    )
    return math.degrees(lam2), math.degrees(phi2)


def polygon_area_km2(lonlat_ring: list[tuple[float, float]]) -> float:
    """Approximate area of a small lon/lat polygon (local equirectangular projection)."""
    if len(lonlat_ring) < 3:
        return 0.0
    mid_lat = math.radians(sum(p[1] for p in lonlat_ring) / len(lonlat_ring))
    deg_km = math.pi * EARTH_RADIUS_KM / 180
    pts = [(lon * deg_km * math.cos(mid_lat), lat * deg_km) for lon, lat in lonlat_ring]
    area = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        area += x1 * y2 - x2 * y1
    return abs(area) / 2
