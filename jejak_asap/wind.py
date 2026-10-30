"""Historical hourly wind from Open-Meteo (ERA5 archive, no key needed)."""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


@dataclass(frozen=True)
class Wind:
    speed_kmh: float
    direction_from_deg: float  # meteorological: where the wind blows FROM

    @property
    def bearing_to_deg(self) -> float:
        """Direction smoke drifts TOWARD."""
        return (self.direction_from_deg + 180.0) % 360.0


def fetch_wind(lon: float, lat: float, date: str, hour_utc: int, level: str = "10m", retries: int = 3) -> Wind:
    """Wind at one place and hour. `level` is an Open-Meteo height suffix such as 10m or 100m."""
    params = {
        "latitude": round(lat, 3),
        "longitude": round(lon, 3),
        "start_date": date,
        "end_date": date,
        "hourly": f"wind_speed_{level},wind_direction_{level}",
        "timezone": "UTC",
    }
    for attempt in range(retries):
        try:
            resp = requests.get(ARCHIVE_URL, params=params, timeout=60)
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))
            continue
        if resp.status_code == 429 and attempt < retries - 1:
            time.sleep(5 * (attempt + 1))
            continue
        resp.raise_for_status()
        hourly = resp.json()["hourly"]
        idx = max(0, min(hour_utc, len(hourly["time"]) - 1))
        return Wind(
            speed_kmh=float(hourly[f"wind_speed_{level}"][idx]),
            direction_from_deg=float(hourly[f"wind_direction_{level}"][idx]),
        )
    raise RuntimeError("Open-Meteo rate limit: try again in a minute")
