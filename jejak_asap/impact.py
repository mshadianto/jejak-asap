"""Stage 2: release particles from accepted plumes and fires, track them, score cities."""

from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .geo import BBox, EARTH_RADIUS_KM, GeoImage
from .transport import Ensemble, Particles, WindGrid, sample_in_polygon, step

WIB = dt.timedelta(hours=7)

# City centres (approximate, degrees). Only cities inside the wind domain are scored.
CITIES: dict[str, tuple[float, float]] = {
    "Palangka Raya": (113.914, -2.210),
    "Banjarmasin": (114.594, -3.319),
    "Banjarbaru": (114.810, -3.457),
    "Kuala Kapuas": (114.390, -3.001),
    "Pulang Pisau": (114.267, -2.743),
    "Sampit": (112.950, -2.531),
    "Kasongan": (113.398, -1.896),
    "Kuala Kurun": (113.870, -1.115),
    "Buntok": (114.842, -1.706),
    "Muara Teweh": (114.894, -0.958),
    "Pangkalan Bun": (111.618, -2.681),
    "Pontianak": (109.343, -0.026),
    "Samarinda": (117.154, -0.502),
    "Balikpapan": (116.853, -1.238),
    "Palembang": (104.775, -2.976),
    "Jambi": (103.613, -1.610),
    "Pekanbaru": (101.448, 0.507),
    "Singapura": (103.820, 1.352),
    "Kuching": (110.359, 1.554),
}


@dataclass
class SourcePlume:
    source_id: int
    rings: list[list[tuple[float, float]]]
    area_km2: float


@dataclass
class FireSource:
    source_id: int
    lon: float
    lat: float
    frp: float


@dataclass
class CityImpact:
    city: str
    lon: float
    lat: float
    risk: str
    max_probability: float
    probability_now: float
    first_arrival_utc: str | None
    likely_arrival_utc: str | None      # first hour at least half the scenarios agree
    likely_arrival_wib: str | None
    peak_utc: str | None
    hours_affected: int
    peak_share: float                   # share of released smoke near the city at the peak hour
    distance_to_nearest_fire_km: float
    probability_by_hour: list[float] = field(default_factory=list)


@dataclass
class RiskRules:
    """Deterministic thresholds. Placeholders until calibrated against a backtest."""

    radius_km: float = 25.0
    high_prob: float = 0.6
    high_share: float = 0.02
    medium_prob: float = 0.3

    def classify(self, max_prob: float, peak_share: float) -> str:
        if max_prob >= self.high_prob and peak_share >= self.high_share:
            return "TINGGI"
        if max_prob >= self.medium_prob:
            return "SEDANG"
        if max_prob > 0:
            return "RENDAH"
        return "AMAN"


@dataclass
class SimulationResult:
    times: list[dt.datetime]
    snapshots: list[Particles]          # positions at each whole hour (index = hour)
    members: int


def load_stage1(geojson_path: Path) -> tuple[list[SourcePlume], list[FireSource], dict]:
    data = json.loads(Path(geojson_path).read_text(encoding="utf-8"))
    plumes: dict[int, SourcePlume] = {}
    fires: list[FireSource] = []
    for f in data["features"]:
        props, geom = f["properties"], f["geometry"]
        if props.get("kind") == "smoke_plume" and props.get("accepted"):
            ring = [(float(x), float(y)) for x, y in geom["coordinates"][0]]
            # area_km2 is the whole object's area, repeated on each of its rings.
            sp = plumes.setdefault(props["object_id"],
                                   SourcePlume(props["object_id"], [], float(props.get("area_km2") or 0.0)))
            sp.rings.append(ring)
        elif props.get("kind") == "fire_cluster":
            lon, lat = geom["coordinates"]
            fires.append(FireSource(props["cluster_id"], float(lon), float(lat), float(props.get("frp_sum_mw") or 0.0)))
    return list(plumes.values()), fires, data.get("properties", {})


def simulate(grid: WindGrid, plumes: list[SourcePlume], fires: list[FireSource], hours: int = 24,
             members: int = 20, initial_per_member: int = 300, fire_release_per_member_hour: int = 6,
             plume_age_h: float = 6.0, diffusion_km: float = 2.0, seed: int = 11,
             plume_at_h: int = 0) -> SimulationResult:
    """Track smoke for `hours` hours from grid.times[0].

    Initial particles fill the accepted plume polygons (smoke already in the air,
    mass in proportion to plume area). Fires keep burning, so each hour new
    particles leave the fire clusters (mass in proportion to FRP) at a rate that
    would rebuild the observed plumes in `plume_age_h` hours. `plume_at_h` puts the
    plume particles in at that hour instead of at the start (after a fires-only spin-up).
    """
    rng = np.random.default_rng(seed)
    ens = Ensemble.make(members)

    total_area = sum(p.area_km2 for p in plumes) or 1.0
    lon, lat, mass, mem, rel, src = [], [], [], [], [], []
    for m in range(members):
        for p in plumes:
            n = max(1, round(initial_per_member * p.area_km2 / total_area))
            ring_areas = np.array([max(_ring_area(r), 1e-9) for r in p.rings])
            counts = np.maximum(1, np.round(n * ring_areas / ring_areas.sum()).astype(int))
            for ring, k, ra in zip(p.rings, counts, ring_areas):
                pts = sample_in_polygon(ring, int(k), rng)
                if not len(pts):
                    continue
                lon += list(pts[:, 0]); lat += list(pts[:, 1])
                ring_mass = p.area_km2 / total_area * ra / ring_areas.sum()
                mass += [ring_mass / len(pts)] * len(pts)
                mem += [m] * len(pts); rel += [0] * len(pts); src += [p.source_id] * len(pts)
    initial_mass_per_member = 1.0
    hourly_mass = initial_mass_per_member / plume_age_h
    frp_total = sum(f.frp for f in fires) or 1.0

    plume_p = Particles(np.array(lon, dtype=float), np.array(lat, dtype=float), np.array(mass, dtype=float),
                        np.array(mem, dtype=int), np.full(len(lon), plume_at_h, dtype=int), np.array(src, dtype=int))
    if plume_at_h <= 0:
        p = plume_p
    else:
        p = Particles(*(np.empty(0, dtype=getattr(plume_p, k).dtype)
                        for k in ("lon", "lat", "mass", "member", "release_h", "source")))
    snapshots = [_copy(p)]
    for h in range(hours):
        if plume_at_h > 0 and h == plume_at_h:
            p = _concat(p, plume_p)
        if fires and fire_release_per_member_hour > 0:
            new = _fire_release(fires, members, fire_release_per_member_hour, hourly_mass, frp_total, h, rng)
            p = _concat(p, new)
        step(grid, p, float(h), 1.0, ens, diffusion_km, rng)
        snapshots.append(_copy(p))
    return SimulationResult(times=grid.times[: hours + 1], snapshots=snapshots, members=members)


def _fire_release(fires, members, per_member, hourly_mass, frp_total, h, rng) -> Particles:
    weights = np.array([max(f.frp, 0.1) for f in fires])
    weights = weights / weights.sum()
    lon, lat, mass, mem, src = [], [], [], [], []
    for m in range(members):
        idx = rng.choice(len(fires), size=per_member, p=weights)
        for i in idx:
            f = fires[i]
            # Start within ~2 km of the cluster centre.
            lon.append(f.lon + rng.normal(0, 0.02)); lat.append(f.lat + rng.normal(0, 0.02))
            mass.append(hourly_mass / per_member)  # FRP already sets how often a fire is picked
            mem.append(m); src.append(-f.source_id)
    n = len(lon)
    return Particles(np.array(lon), np.array(lat), np.array(mass), np.array(mem, dtype=int),
                     np.full(n, h + 1, dtype=int), np.array(src, dtype=int))


def _ring_area(ring) -> float:
    pts = np.asarray(ring)
    x, y = pts[:, 0], pts[:, 1]
    return abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2


def _copy(p: Particles) -> Particles:
    return Particles(p.lon.copy(), p.lat.copy(), p.mass.copy(), p.member.copy(), p.release_h.copy(), p.source.copy())


def _concat(a: Particles, b: Particles) -> Particles:
    return Particles(*(np.concatenate([getattr(a, k), getattr(b, k)])
                       for k in ("lon", "lat", "mass", "member", "release_h", "source")))


def haversine_km(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(np.radians, (lon1, lat1, lon2, lat2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


def score_cities(sim: SimulationResult, cities: dict[str, tuple[float, float]], domain: BBox,
                 fires: list[FireSource], rules: RiskRules = RiskRules()) -> list[CityImpact]:
    out = []
    for name, (clon, clat) in cities.items():
        if not domain.contains(clon, clat):
            continue
        probs, shares = [], []
        for snap in sim.snapshots:
            near = haversine_km(snap.lon, snap.lat, clon, clat) <= rules.radius_km
            hit_members = np.unique(snap.member[near])
            probs.append(len(hit_members) / sim.members)
            # Mass near the city divided by all mass in the air, averaged over members.
            total = np.bincount(snap.member, weights=snap.mass, minlength=sim.members).astype(float)
            local = np.bincount(snap.member[near], weights=snap.mass[near], minlength=sim.members).astype(float)
            shares.append(float(np.mean(np.divide(local, total, out=np.zeros_like(local), where=total > 0))))
        probs_a, shares_a = np.array(probs), np.array(shares)
        t = sim.times
        first = next((i for i, v in enumerate(probs) if v > 0), None)
        likely = next((i for i, v in enumerate(probs) if v >= 0.5), None)
        peak = int(np.argmax(shares_a)) if shares_a.max() > 0 else None
        dist = float(min(haversine_km(f.lon, f.lat, clon, clat) for f in fires)) if fires else float("nan")
        out.append(CityImpact(
            city=name, lon=clon, lat=clat,
            risk=rules.classify(float(probs_a.max()), float(shares_a.max())),
            max_probability=round(float(probs_a.max()), 2),
            probability_now=round(float(probs_a[0]), 2),
            first_arrival_utc=None if first is None else t[first].isoformat(),
            likely_arrival_utc=None if likely is None else t[likely].isoformat(),
            likely_arrival_wib=None if likely is None else (t[likely] + WIB).strftime("%d %b %H.%M WIB"),
            peak_utc=None if peak is None else t[peak].isoformat(),
            hours_affected=int((probs_a >= 0.5).sum()),
            peak_share=round(float(shares_a.max()), 4),
            distance_to_nearest_fire_km=round(dist, 1),
            probability_by_hour=[round(float(v), 2) for v in probs],
        ))
    order = {"TINGGI": 0, "SEDANG": 1, "RENDAH": 2, "AMAN": 3}
    out.sort(key=lambda c: (order[c.risk], -c.max_probability, c.distance_to_nearest_fire_km))
    return out


RISK_COLORS = {"TINGGI": (220, 38, 38), "SEDANG": (245, 158, 11), "RENDAH": (234, 210, 60), "AMAN": (34, 197, 94)}


def _font(size: int):
    for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "arialbd.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_frame(base: Image.Image, geo: GeoImage, snap: Particles, cities: list[CityImpact],
                 label: str, plumes: list[SourcePlume] | None = None, trail: list[Particles] | None = None) -> Image.Image:
    img = base.convert("RGB").copy()
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    if plumes:
        for p in plumes:
            for ring in p.rings:
                d.polygon([geo.to_pixel(lon, lat) for lon, lat in ring], outline=(60, 230, 120, 255))
    for k, older in enumerate(trail or []):
        alpha = 25 + 40 * k // max(1, len(trail) - 1)
        for x, y in _pixels(geo, older):
            d.point((x, y), fill=(255, 140, 0, alpha))
    for x, y in _pixels(geo, snap):
        d.ellipse((x - 1, y - 1, x + 1, y + 1), fill=(255, 90, 20, 170))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    d = ImageDraw.Draw(img)
    small, big = _font(max(12, img.width // 90)), _font(max(16, img.width // 55))
    for c in cities:
        if not geo.bbox.contains(c.lon, c.lat):
            continue
        x, y = geo.to_pixel(c.lon, c.lat)
        col = RISK_COLORS[c.risk]
        d.ellipse((x - 7, y - 7, x + 7, y + 7), fill=col, outline=(255, 255, 255), width=2)
        text = f"{c.city}: {c.risk}"
        d.text((x + 10, y - 9), text, fill=(255, 255, 255), font=small, stroke_width=3, stroke_fill=(0, 0, 0))
    d.text((14, 12), label, fill=(255, 255, 255), font=big, stroke_width=3, stroke_fill=(0, 0, 0))
    return img


def _pixels(geo: GeoImage, p: Particles):
    inside = (p.lon >= geo.bbox.west) & (p.lon <= geo.bbox.east) & (p.lat >= geo.bbox.south) & (p.lat <= geo.bbox.north)
    for lon, lat in zip(p.lon[inside], p.lat[inside]):
        yield geo.to_pixel(float(lon), float(lat))


def write_outputs(out_dir: Path, sim: SimulationResult, impacts: list[CityImpact], base: Image.Image,
                  geo: GeoImage, plumes: list[SourcePlume], meta: dict, gif_step_h: int = 2,
                  gif_width: int = 900) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "impact.json").write_text(
        json.dumps({**meta, "cities": [asdict(c) for c in impacts]}, indent=1, default=str), encoding="utf-8")
    frames = []
    for h in range(0, len(sim.snapshots), gif_step_h):
        when = sim.times[h] + WIB
        label = f"Jejak Asap  +{h:02d} jam  ({when:%d %b %Y %H.%M} WIB)"
        frame = render_frame(base, geo, sim.snapshots[h], impacts, label,
                             plumes if h == 0 else None, sim.snapshots[max(0, h - 3):h])
        if frame.width > gif_width:
            frame = frame.resize((gif_width, round(frame.height * gif_width / frame.width)), Image.LANCZOS)
        frames.append(frame.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
    frames[0].save(out_dir / "forecast.gif", save_all=True, append_images=frames[1:], duration=600, loop=0,
                   optimize=True)
    last = len(sim.snapshots) - 1
    render_frame(base, geo, sim.snapshots[last], impacts,
                 f"Jejak Asap: posisi asap +{last} jam dan risiko per kota", plumes,
                 sim.snapshots[1:last:2]).save(out_dir / "forecast_map.png")
