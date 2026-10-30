"""Stage 2: move smoke particles with gridded hourly wind.

This is a Lagrangian trajectory model, deliberately simple: particles follow the
horizontal wind at one height, an ensemble of perturbed winds stands in for
forecast and plume-height uncertainty, and no chemistry or deposition is modelled.
It predicts where and when smoke goes, not how concentrated it is.
"""

from __future__ import annotations

import datetime as dt
import math
import time
from dataclasses import dataclass

import numpy as np
import requests

from .geo import BBox, EARTH_RADIUS_KM

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"    # ERA5 reanalysis (backtests)
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"         # live forecasts
KM_PER_DEG = math.pi * EARTH_RADIUS_KM / 180


@dataclass
class WindGrid:
    """Hourly u/v wind (km/h) on a regular lon/lat grid."""

    lons: np.ndarray            # (nx,) ascending
    lats: np.ndarray            # (ny,) ascending
    times: list[dt.datetime]    # (nt,) hourly, UTC
    u: np.ndarray               # (nt, ny, nx) eastward km/h
    v: np.ndarray               # (nt, ny, nx) northward km/h

    def sample(self, t_hours: float, lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Bilinear in space, linear in time. `t_hours` counts from times[0]."""
        t = float(np.clip(t_hours, 0, len(self.times) - 1))
        i0 = min(int(math.floor(t)), len(self.times) - 2) if len(self.times) > 1 else 0
        w = t - i0 if len(self.times) > 1 else 0.0
        u0, v0 = self._bilinear(self.u[i0], lon, lat), self._bilinear(self.v[i0], lon, lat)
        if len(self.times) == 1:
            return u0, v0
        u1, v1 = self._bilinear(self.u[i0 + 1], lon, lat), self._bilinear(self.v[i0 + 1], lon, lat)
        return u0 * (1 - w) + u1 * w, v0 * (1 - w) + v1 * w

    def _bilinear(self, field: np.ndarray, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        fx = np.interp(lon, self.lons, np.arange(len(self.lons)))
        fy = np.interp(lat, self.lats, np.arange(len(self.lats)))
        x0 = np.clip(np.floor(fx).astype(int), 0, max(len(self.lons) - 2, 0))
        y0 = np.clip(np.floor(fy).astype(int), 0, max(len(self.lats) - 2, 0))
        x1 = np.minimum(x0 + 1, len(self.lons) - 1)
        y1 = np.minimum(y0 + 1, len(self.lats) - 1)
        wx, wy = fx - x0, fy - y0
        return ((field[y0, x0] * (1 - wx) + field[y0, x1] * wx) * (1 - wy)
                + (field[y1, x0] * (1 - wx) + field[y1, x1] * wx) * wy)

    def contains(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        return (lon >= self.lons[0]) & (lon <= self.lons[-1]) & (lat >= self.lats[0]) & (lat <= self.lats[-1])


def speed_dir_to_uv(speed_kmh: np.ndarray, dir_from_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    rad = np.radians(dir_from_deg)
    return -speed_kmh * np.sin(rad), -speed_kmh * np.cos(rad)


def fetch_wind_grid(domain: BBox, start: dt.datetime, hours: int, spacing_deg: float = 1.0,
                    level: str = "100m", source: str = "archive", chunk: int = 60,
                    retries: int = 3) -> WindGrid:
    """Hourly wind on a grid covering `domain`, from `start` (UTC, whole hour) for `hours` hours.

    100 m wind is the default: smoke from peat fires mostly travels in the lowest
    few hundred metres, and 10 m wind is slowed by surface friction.
    """
    lons = np.arange(math.floor(domain.west), math.ceil(domain.east) + 1e-9, spacing_deg)
    lats = np.arange(math.floor(domain.south), math.ceil(domain.north) + 1e-9, spacing_deg)
    pts = [(la, lo) for la in lats for lo in lons]
    end = start + dt.timedelta(hours=hours)
    url = ARCHIVE_URL if source == "archive" else FORECAST_URL
    speeds, dirs, times = [], [], None
    for k in range(0, len(pts), chunk):
        part = pts[k:k + chunk]
        params = {
            "latitude": ",".join(f"{la:.3f}" for la, _ in part),
            "longitude": ",".join(f"{lo:.3f}" for _, lo in part),
            "hourly": f"wind_speed_{level},wind_direction_{level}",
            "start_date": start.date().isoformat(),
            "end_date": end.date().isoformat(),
            "timezone": "UTC",
        }
        data = _get_json(url, params, retries)
        items = data if isinstance(data, list) else [data]
        for item in items:
            hourly = item["hourly"]
            if times is None:
                times = [dt.datetime.fromisoformat(t) for t in hourly["time"]]
            speeds.append(hourly[f"wind_speed_{level}"])
            dirs.append(hourly[f"wind_direction_{level}"])
    assert times is not None
    i0 = times.index(start.replace(tzinfo=None))
    sel = slice(i0, i0 + hours + 1)
    spd = np.array(speeds, dtype=float)[:, sel]      # (npts, nt)
    dirn = np.array(dirs, dtype=float)[:, sel]
    spd, dirn = np.nan_to_num(spd), np.nan_to_num(dirn)
    u, v = speed_dir_to_uv(spd, dirn)
    shape = (len(lats), len(lons), -1)
    return WindGrid(lons=lons, lats=lats, times=times[sel],
                    u=np.moveaxis(u.reshape(shape), -1, 0), v=np.moveaxis(v.reshape(shape), -1, 0))


def _get_json(url: str, params: dict, retries: int):
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=60)
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))
            continue
        if resp.status_code == 429 and attempt < retries - 1:
            time.sleep(10 * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("Open-Meteo request failed")


@dataclass
class Particles:
    lon: np.ndarray
    lat: np.ndarray
    mass: np.ndarray       # relative smoke amount carried
    member: np.ndarray     # ensemble member index
    release_h: np.ndarray  # hour of release (0 = analysis time)
    source: np.ndarray     # object or cluster id the particle came from


@dataclass
class Ensemble:
    rot_deg: np.ndarray    # per-member wind direction perturbation
    scale: np.ndarray      # per-member wind speed factor

    @classmethod
    def make(cls, members: int, rot_sd: float = 15.0, speed_sd: float = 0.2, seed: int = 7) -> "Ensemble":
        rng = np.random.default_rng(seed)
        rot = rng.normal(0, rot_sd, members)
        scale = np.clip(rng.normal(1.0, speed_sd, members), 0.4, 1.8)
        rot[0], scale[0] = 0.0, 1.0  # member 0 is the unperturbed wind
        return cls(rot, scale)


def perturb(u: np.ndarray, v: np.ndarray, rot_deg: np.ndarray, scale: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    r = np.radians(rot_deg)
    cos, sin = np.cos(r), np.sin(r)
    # Rotating the wind vector clockwise by `rot` (compass convention).
    return scale * (u * cos + v * sin), scale * (-u * sin + v * cos)


def step(grid: WindGrid, p: Particles, t_h: float, dt_h: float, ens: Ensemble,
         diffusion_km: float, rng: np.random.Generator) -> None:
    """Advance particles by dt_h hours with a midpoint (RK2) scheme plus a small random walk."""
    rot, scale = ens.rot_deg[p.member], ens.scale[p.member]

    def velocity(t, lon, lat):
        u, v = grid.sample(t, lon, lat)
        return perturb(u, v, rot, scale)

    u1, v1 = velocity(t_h, p.lon, p.lat)
    mid_lon, mid_lat = _move(p.lon, p.lat, u1, v1, dt_h / 2)
    u2, v2 = velocity(t_h + dt_h / 2, mid_lon, mid_lat)
    lon, lat = _move(p.lon, p.lat, u2, v2, dt_h)
    if diffusion_km > 0:
        sd = diffusion_km * math.sqrt(dt_h)
        lon, lat = _move(lon, lat, rng.normal(0, sd, lon.shape), rng.normal(0, sd, lat.shape), 1.0)
    p.lon, p.lat = lon, lat


def _move(lon: np.ndarray, lat: np.ndarray, u_kmh: np.ndarray, v_kmh: np.ndarray, hours: float):
    dlat = v_kmh * hours / KM_PER_DEG
    dlon = u_kmh * hours / (KM_PER_DEG * np.cos(np.radians(lat)))
    return lon + dlon, lat + dlat


def sample_in_polygon(ring: list[tuple[float, float]], n: int, rng: np.random.Generator) -> np.ndarray:
    """Uniform random points inside a lon/lat polygon (rejection sampling)."""
    poly = np.asarray(ring, dtype=float)
    lo, hi = poly.min(axis=0), poly.max(axis=0)
    out = np.empty((0, 2))
    tries = 0
    while len(out) < n and tries < 50:
        cand = rng.uniform(lo, hi, size=(max(n * 3, 64), 2))
        out = np.vstack([out, cand[points_in_polygon(cand, poly)]])
        tries += 1
    return out[:n]


def points_in_polygon(pts: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Even-odd ray casting, vectorised over points."""
    x, y = pts[:, 0][:, None], pts[:, 1][:, None]
    x1, y1 = poly[:, 0][None, :], poly[:, 1][None, :]
    x2, y2 = np.roll(poly[:, 0], -1)[None, :], np.roll(poly[:, 1], -1)[None, :]
    crosses = ((y1 > y) != (y2 > y)) & (x < (x2 - x1) * (y - y1) / np.where(y2 == y1, 1e-12, y2 - y1) + x1)
    return crosses.sum(axis=1) % 2 == 1
