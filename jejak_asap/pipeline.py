"""Stage 1: fire hotspots -> SAM 3.1 smoke masks -> Muse QA -> GeoJSON plumes."""

from __future__ import annotations

import hashlib
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from .firms import FireCluster, Hotspot, cluster_hotspots, filter_hotspots
from .geo import GeoImage, destination, polygon_area_km2
from .muse import MuseVerdict, check_object, is_smoke
from .sam import ObjectSeed, SegmentedObject, build_prompt, call_sam, parse_sam_output
from .wind import Wind, fetch_wind


@dataclass
class SeedInfo:
    cluster_id: int
    hotspot_count: int
    frp_sum: float
    fire_lonlat: tuple[float, float]
    seed_lonlats: list[tuple[float, float]]
    seed_pxs: list[tuple[int, int]]
    anchor_lonlats: list[tuple[float, float]]


@dataclass
class PlumeResult:
    object_id: int
    sam_confidence: float | None
    area_km2: float
    accepted: bool
    muse: dict | None
    polygons: list[list[tuple[float, float]]] = field(default_factory=list)


class IncompleteRun(Exception):
    """Raised when the time budget runs out; rerun with --resume to continue."""


def _check_budget(deadline: float | None, stage: str) -> None:
    if deadline is not None and time.monotonic() > deadline:
        raise IncompleteRun(f"time budget used up after {stage}")


def _raw_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def save_objects(path: Path, raw: str, objects: list[SegmentedObject]) -> None:
    """Decoded masks are slow to rebuild (pure-Python decoder), so keep them packed."""
    np.savez_compressed(
        path,
        raw_hash=np.array(_raw_hash(raw)),
        ids=np.array([o.object_id for o in objects], dtype=np.int32),
        boxes=np.array([o.box for o in objects], dtype=np.float64).reshape(-1, 4),
        conf=np.array([np.nan if o.confidence is None else o.confidence for o in objects], dtype=np.float64),
        masks=np.packbits(np.stack([o.mask for o in objects]) if objects else np.zeros((0, 1, 1), bool), axis=-1),
        shape=np.array(objects[0].mask.shape if objects else (0, 0)),
    )


def load_objects(path: Path, raw: str, geo: GeoImage) -> list[SegmentedObject] | None:
    if not path.exists():
        return None
    data = np.load(path)
    if str(data["raw_hash"]) != _raw_hash(raw):
        return None
    h, w = (int(v) for v in data["shape"])
    if len(data["ids"]) and (h, w) != (geo.height, geo.width):
        return None
    masks = np.unpackbits(data["masks"], axis=-1, count=w).astype(bool) if len(data["ids"]) else []
    return [
        SegmentedObject(object_id=int(i), box=tuple(float(v) for v in b), mask=m,
                        confidence=None if np.isnan(c) else float(c))
        for i, b, c, m in zip(data["ids"], data["boxes"], data["conf"], masks)
    ]


def run_muse_checks(client, model: str, image: Image.Image, objects: list[SegmentedObject], out_dir: Path,
                    use_muse: bool, max_area_frac: float, deadline: float | None, log,
                    workers: int = 5) -> dict[int, MuseVerdict]:
    """Size gate first, then Muse in parallel; each verdict is cached on disk."""
    total_px = image.width * image.height
    verdicts: dict[int, MuseVerdict] = {}
    todo: list[SegmentedObject] = []
    for obj in objects:
        frac = float(obj.mask.sum()) / total_px
        if not obj.mask.any():
            continue
        if frac > max_area_frac:
            # A point on a uniform haze or cloud deck can make SAM return most of
            # the frame; that is not one plume.
            verdicts[obj.object_id] = MuseVerdict("other", None, None, f"mask covers {frac:.0%} of the image", "")
            log(f"  object {obj.object_id}: rejected, covers {frac:.0%} of the image")
            continue
        if not use_muse:
            continue
        cached = out_dir / f"muse_{obj.object_id}.json"
        if cached.exists():
            try:
                data = json.loads(cached.read_text(encoding="utf-8"))
                if data.get("mask_px") == int(obj.mask.sum()):
                    verdicts[obj.object_id] = MuseVerdict(**{k: data[k] for k in ("label", "smoke_fraction", "confidence", "reason")}, raw="")
                    continue
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
        todo.append(obj)

    def one(obj: SegmentedObject) -> tuple[SegmentedObject, MuseVerdict]:
        try:
            return obj, check_object(client, model, image, obj)
        except Exception as exc:  # one failed check must not sink the run
            return obj, MuseVerdict("other", None, None, f"Muse check failed: {exc}", "")

    if todo:
        _check_budget(deadline, "before Muse checks")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for obj, verdict in pool.map(one, todo):
                verdicts[obj.object_id] = verdict
                log(f"  object {obj.object_id}: Muse says {verdict.label} ({verdict.reason})")
                if not verdict.reason.startswith("Muse check failed"):
                    payload = {k: v for k, v in asdict(verdict).items() if k != "raw"}
                    payload["mask_px"] = int(obj.mask.sum())
                    (out_dir / f"muse_{obj.object_id}.json").write_text(json.dumps(payload), encoding="utf-8")
    return verdicts


def dominant_hour(hotspots: list[Hotspot]) -> int:
    """Median UTC hour of daytime detections: the pass that matches the true-colour image."""
    day = [h for h in hotspots if h.daynight.upper() != "N"] or hotspots
    hours = [int(h.acq_time[:2]) for h in day if h.acq_time[:2].isdigit()]
    return int(statistics.median(hours)) if hours else 6


def make_seeds(clusters: list[FireCluster], geo: GeoImage, wind: Wind | None, offset_km: float,
               points_per_cluster: int = 3) -> tuple[list[ObjectSeed], list[SeedInfo]]:
    """Positive points per fire cluster, each placed downwind of a strong sub-front.

    Smoke is thickest a few km downwind of the fire line; a point on the fire
    itself often lands on dark burned ground instead of smoke.
    """
    seeds, infos = [], []
    for c in clusters:
        anchors = c.anchor_points(max_points=points_per_cluster)
        seed_lls, pxs = [], []
        for fire in anchors:
            seed_ll = fire
            if wind is not None and offset_km > 0:
                moved = destination(fire[0], fire[1], wind.bearing_to_deg, offset_km)
                if geo.bbox.contains(*moved):
                    seed_ll = moved
            px = geo.to_pixel(*seed_ll)
            if px not in pxs:  # the API rejects nothing here, but duplicates add nothing
                seed_lls.append(seed_ll)
                pxs.append(px)
        seeds.append(ObjectSeed(object_id=c.cluster_id, positive=pxs, negative=[]))
        infos.append(SeedInfo(c.cluster_id, len(c.hotspots), round(c.frp_sum, 1), c.centroid, seed_lls, pxs, anchors))
    return seeds, infos


def mask_to_polygons(mask: np.ndarray, geo: GeoImage, min_area_px: int = 50) -> list[list[tuple[float, float]]]:
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    polygons = []
    for cnt in contours:
        if cv2.contourArea(cnt) < min_area_px:
            continue
        approx = cv2.approxPolyDP(cnt, epsilon=1.5, closed=True)
        if len(approx) < 3:
            continue
        ring = [geo.to_lonlat(float(p[0][0]) + 0.5, float(p[0][1]) + 0.5) for p in approx]
        polygons.append(ring)
    return polygons


def to_geojson(plumes: list[PlumeResult], seeds: list[SeedInfo], meta: dict) -> dict:
    features = []
    for p in plumes:
        for ring in p.polygons:
            coords = [[round(lon, 5), round(lat, 5)] for lon, lat in ring]
            coords.append(coords[0])
            features.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [coords]},
                "properties": {
                    "kind": "smoke_plume",
                    "object_id": p.object_id,
                    "accepted": p.accepted,
                    "sam_confidence": p.sam_confidence,
                    "area_km2": round(p.area_km2, 2),
                    "muse_label": (p.muse or {}).get("label"),
                    "muse_reason": (p.muse or {}).get("reason"),
                },
            })
    for s in seeds:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(s.fire_lonlat[0], 5), round(s.fire_lonlat[1], 5)]},
            "properties": {"kind": "fire_cluster", "cluster_id": s.cluster_id,
                           "hotspots": s.hotspot_count, "frp_sum_mw": s.frp_sum},
        })
    return {"type": "FeatureCollection", "properties": meta, "features": features}


def draw_overlay(image: Image.Image, geo: GeoImage, hotspots: list[Hotspot], seeds: list[SeedInfo],
                 objects: list[SegmentedObject], plumes: list[PlumeResult]) -> Image.Image:
    base = np.array(image).astype(np.float32)
    status = {p.object_id: p for p in plumes}
    for obj in objects:
        p = status.get(obj.object_id)
        color = (40, 220, 90) if (p and p.accepted) else (150, 150, 150)
        base[obj.mask] = base[obj.mask] * 0.55 + np.array(color) * 0.45
    out = Image.fromarray(base.clip(0, 255).astype(np.uint8))
    canvas = np.array(out)
    for obj in objects:
        p = status.get(obj.object_id)
        color = (40, 220, 90) if (p and p.accepted) else (150, 150, 150)
        contours, _ = cv2.findContours(obj.mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(canvas, contours, -1, color, 2)
    out = Image.fromarray(canvas)
    draw = ImageDraw.Draw(out)
    for h in hotspots:
        x, y = geo.to_pixel(h.lon, h.lat)
        draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(255, 40, 40))
    for s in seeds:
        for anchor, (x, y) in zip(s.anchor_lonlats, s.seed_pxs):
            fx, fy = geo.to_pixel(*anchor)
            draw.line((fx, fy, x, y), fill=(255, 230, 0), width=2)
            draw.ellipse((x - 6, y - 6, x + 6, y + 6), outline=(255, 230, 0), width=3)
            draw.text((x + 8, y - 8), str(s.cluster_id), fill=(255, 255, 0))
    return out


def run_stage1(*, image: Image.Image, geo: GeoImage, hotspots: list[Hotspot], date: str, out_dir: Path,
               client=None, sam_model: str = "sam-3.1", muse_model: str = "muse-spark-1.3",
               wind: Wind | None = None, offset_km: float = 6.0, use_muse: bool = True,
               max_clusters: int = 16, min_hotspots: int = 2, max_area_frac: float = 0.5,
               reuse_sam: bool = False, deadline: float | None = None, sam_batch: int = 4,
               log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    if not (out_dir / "image.jpg").exists():
        image.save(out_dir / "image.jpg", quality=95)

    usable = filter_hotspots(hotspots, geo.bbox)
    clusters = cluster_hotspots(usable, max_clusters=max_clusters, min_hotspots=min_hotspots)
    log(f"hotspots: {len(hotspots)} total, {len(usable)} usable, {len(clusters)} clusters prompted")
    if not clusters:
        raise RuntimeError("No usable daytime hotspots in this bbox and date.")

    seeds, seed_infos = make_seeds(clusters, geo, wind, offset_km)
    prompt_text = build_prompt(seeds, (geo.width, geo.height))
    (out_dir / "sam_prompt.txt").write_text(prompt_text, encoding="utf-8")

    meta = {"date": date, "bbox": asdict(geo.bbox), "image_size": [geo.width, geo.height],
            "wind": asdict(wind) if wind else None, "offset_km": offset_km,
            "sam_model": sam_model, "muse_model": muse_model if use_muse else None}

    objects: list[SegmentedObject] = []
    plumes: list[PlumeResult] = []
    if client is None:
        log("dry run: no Meta API key, stopping after building the SAM prompt")
    else:
        # SAM is called in small batches of objects; every batch saves its raw output and
        # decoded masks, so an interrupted run resumes at the first unfinished batch.
        for k in range(0, len(seeds), sam_batch):
            batch = seeds[k:k + sam_batch]
            tag = f"{k // sam_batch:02d}"
            batch_prompt = build_prompt(batch, (geo.width, geo.height))
            raw_path, prompt_cache = out_dir / f"sam_raw_{tag}.txt", out_dir / f"sam_prompt_{tag}.txt"
            if reuse_sam and raw_path.exists() and prompt_cache.exists() \
                    and prompt_cache.read_text(encoding="utf-8") == batch_prompt:
                raw = raw_path.read_text(encoding="utf-8")
            else:
                raw = call_sam(client, sam_model, image, batch_prompt)
                raw_path.write_text(raw, encoding="utf-8")
                prompt_cache.write_text(batch_prompt, encoding="utf-8")
                log(f"SAM batch {tag}: {len(batch)} objects prompted")
                _check_budget(deadline, f"SAM batch {tag}")
            # Mask caches are keyed by a hash of the raw output, so a new SAM answer
            # never reuses masks (or Muse verdicts) from an older one.
            npz = out_dir / f"objects_{tag}.npz"
            found = load_objects(npz, raw, geo)
            if found is None:
                found = parse_sam_output(raw, geo.width, geo.height)
                save_objects(npz, raw, found)
                _check_budget(deadline, f"mask decoding {tag}")
            objects.extend(found)
        log(f"SAM returned {len(objects)} objects")

        verdicts = run_muse_checks(client, muse_model, image, objects, out_dir, use_muse,
                                   max_area_frac, deadline, log)
        kmx, kmy = geo.km_per_pixel()
        for obj in objects:
            verdict = verdicts.get(obj.object_id)
            accepted = bool(obj.mask.any()) and (verdict is None or is_smoke(verdict))
            plumes.append(PlumeResult(
                object_id=obj.object_id,
                sam_confidence=obj.confidence,
                area_km2=float(obj.mask.sum()) * kmx * kmy,
                accepted=accepted,
                muse=None if verdict is None else {k: v for k, v in asdict(verdict).items() if k != "raw"},
                polygons=mask_to_polygons(obj.mask, geo),
            ))

    geojson = to_geojson(plumes, seed_infos, meta)
    (out_dir / "plumes.geojson").write_text(json.dumps(geojson, indent=1), encoding="utf-8")
    draw_overlay(image, geo, usable, seed_infos, objects, plumes).save(out_dir / "overlay.png")

    report = {
        **meta,
        "hotspots_total": len(hotspots),
        "hotspots_usable": len(usable),
        "clusters": [asdict(s) for s in seed_infos],
        "plumes": [{k: v for k, v in asdict(p).items() if k != "polygons"} for p in plumes],
        "accepted_plumes": sum(p.accepted for p in plumes),
        "accepted_area_km2": round(sum(p.area_km2 for p in plumes if p.accepted), 1),
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return report
