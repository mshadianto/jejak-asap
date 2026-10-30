"""Active-fire hotspots from NASA FIRMS, and clustering them into prompt seeds."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

import requests

from .geo import BBox

FIRMS_AREA_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/{area}/{days}/{date}"

# Standard Processing (SP) for archived events, Near Real-Time (NRT) for recent days.
DEFAULT_SOURCE = "VIIRS_SNPP_SP"


@dataclass(frozen=True)
class Hotspot:
    lon: float
    lat: float
    frp: float  # fire radiative power, MW
    confidence: str
    acq_date: str
    acq_time: str  # HHMM, UTC
    daynight: str


@dataclass
class FireCluster:
    """Hotspots grouped into one fire front; seeds one SAM object prompt."""

    cluster_id: int
    hotspots: list[Hotspot] = field(default_factory=list)

    @property
    def frp_sum(self) -> float:
        return sum(h.frp for h in self.hotspots)

    @property
    def centroid(self) -> tuple[float, float]:
        return _weighted_centroid(self.hotspots)

    def anchor_points(self, max_points: int = 3, cell_deg: float = 0.02) -> list[tuple[float, float]]:
        """The strongest sub-fronts of a large cluster, as FRP-weighted centres.

        A long fire line can bend; its overall centroid may fall between two
        plumes, so a big cluster gets one point per strongest ~2 km sub-cell.
        """
        cells: dict[tuple[int, int], list[Hotspot]] = {}
        for h in self.hotspots:
            cells.setdefault((int(h.lon // cell_deg), int(h.lat // cell_deg)), []).append(h)
        ranked = sorted(cells.values(), key=lambda g: sum(h.frp for h in g), reverse=True)
        return [_weighted_centroid(g) for g in ranked[:max_points]]


def _weighted_centroid(hotspots: list[Hotspot]) -> tuple[float, float]:
    weights = [max(h.frp, 0.1) for h in hotspots]
    total = sum(weights)
    lon = sum(h.lon * w for h, w in zip(hotspots, weights)) / total
    lat = sum(h.lat * w for h, w in zip(hotspots, weights)) / total
    return lon, lat


def fetch_hotspots(map_key: str, bbox: BBox, date: str, source: str = DEFAULT_SOURCE, days: int = 1) -> list[Hotspot]:
    url = FIRMS_AREA_URL.format(key=map_key, source=source, area=bbox.as_firms_area(), days=days, date=date)
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    text = resp.text
    if not text.lstrip().lower().startswith("latitude"):
        # FIRMS reports key and quota problems as plain text with HTTP 200.
        raise RuntimeError(f"FIRMS did not return CSV: {text[:200]!r}")
    return parse_firms_csv(text)


GIBS_WMS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"


def hotspots_from_gibs(bbox: BBox, date: str, width: int, height: int,
                       layer: str = "VIIRS_SNPP_Thermal_Anomalies_375m_All") -> list[Hotspot]:
    """Approximate hotspots read from GIBS's rendered fire layer (no key needed).

    Fallback for demos and tests only: each drawn fire blob becomes one hotspot at
    its centre, FRP is replaced by blob size in pixels, and the time is assumed to
    be the Suomi NPP afternoon pass (~06:30 UTC over western Indonesia). Use the
    FIRMS API for real FRP, confidence and acquisition times.
    """
    import cv2
    import numpy as np
    from PIL import Image

    params = {
        "SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": "1.1.1", "LAYERS": layer, "STYLES": "",
        "SRS": "EPSG:4326", "BBOX": f"{bbox.west},{bbox.south},{bbox.east},{bbox.north}",
        "WIDTH": width, "HEIGHT": height, "FORMAT": "image/png", "TRANSPARENT": "TRUE", "TIME": date,
    }
    resp = requests.get(GIBS_WMS, params=params, timeout=120)
    resp.raise_for_status()
    rgba = np.array(Image.open(io.BytesIO(resp.content)).convert("RGBA"))
    drawn = ((rgba[..., 3] > 0) & (rgba[..., 0] > 100)).astype(np.uint8)
    count, _, stats, centroids = cv2.connectedComponentsWithStats(drawn, connectivity=8)
    out = []
    for i in range(1, count):
        cx, cy = centroids[i]
        lon = bbox.west + (cx + 0.5) / width * bbox.lon_span
        lat = bbox.north - (cy + 0.5) / height * bbox.lat_span
        out.append(Hotspot(lon, lat, float(stats[i, cv2.CC_STAT_AREA]), "n", date, "0630", "D"))
    return out


def load_hotspots_csv(path: Path) -> list[Hotspot]:
    return parse_firms_csv(Path(path).read_text(encoding="utf-8"))


def parse_firms_csv(text: str) -> list[Hotspot]:
    rows = csv.DictReader(io.StringIO(text))
    out: list[Hotspot] = []
    for row in rows:
        try:
            out.append(
                Hotspot(
                    lon=float(row["longitude"]),
                    lat=float(row["latitude"]),
                    frp=float(row.get("frp") or 0.0),
                    confidence=str(row.get("confidence", "")),
                    acq_date=row.get("acq_date", ""),
                    acq_time=str(row.get("acq_time", "")).zfill(4),
                    daynight=row.get("daynight", ""),
                )
            )
        except (KeyError, ValueError):
            continue
    return out


def filter_hotspots(hotspots: list[Hotspot], bbox: BBox, daytime_only: bool = True, drop_low_confidence: bool = True) -> list[Hotspot]:
    """Keep detections that can have smoke visible in a daytime true-colour image."""
    kept = []
    for h in hotspots:
        if not bbox.contains(h.lon, h.lat):
            continue
        if daytime_only and h.daynight.upper() == "N":
            continue
        if drop_low_confidence and h.confidence.lower() in {"l", "low"}:
            continue
        kept.append(h)
    return kept


def cluster_hotspots(hotspots: list[Hotspot], cell_deg: float = 0.05, max_clusters: int = 16,
                     min_hotspots: int = 1) -> list[FireCluster]:
    """Group hotspots into grid cells, merge touching cells, keep the strongest fronts.

    0.05 degrees is about 5.5 km. SAM 3.1 accepts at most 16 prompted objects per
    request, so only the `max_clusters` clusters with the highest summed FRP are kept.
    Clusters smaller than `min_hotspots` rarely carry a visible plume and are dropped.
    """
    cells: dict[tuple[int, int], list[Hotspot]] = {}
    for h in hotspots:
        key = (int(h.lon // cell_deg), int(h.lat // cell_deg))
        cells.setdefault(key, []).append(h)

    # Connected components over the 8-neighbourhood of occupied cells.
    seen: set[tuple[int, int]] = set()
    groups: list[list[Hotspot]] = []
    for start in cells:
        if start in seen:
            continue
        stack, members = [start], []
        seen.add(start)
        while stack:
            cx, cy = stack.pop()
            members.extend(cells[(cx, cy)])
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    nb = (cx + dx, cy + dy)
                    if nb in cells and nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
        if len(members) >= min_hotspots:
            groups.append(members)

    groups.sort(key=lambda g: sum(h.frp for h in g), reverse=True)
    return [FireCluster(cluster_id=i + 1, hotspots=g) for i, g in enumerate(groups[:max_clusters])]
