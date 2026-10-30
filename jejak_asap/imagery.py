"""True-colour satellite imagery from NASA GIBS (no key needed)."""

from __future__ import annotations

import io
from pathlib import Path

import requests
from PIL import Image

from .geo import BBox, GeoImage, image_size_for

GIBS_WMS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"

# VIIRS on Suomi NPP passes around 13:30 local time, matching VIIRS_SNPP fire detections.
DEFAULT_LAYER = "VIIRS_SNPP_CorrectedReflectance_TrueColor"


def fetch_true_color(bbox: BBox, date: str, width: int = 1600, layer: str = DEFAULT_LAYER) -> tuple[Image.Image, GeoImage]:
    width, height = image_size_for(bbox, width)
    params = {
        "SERVICE": "WMS",
        "REQUEST": "GetMap",
        "VERSION": "1.1.1",  # 1.1.1 keeps lon,lat axis order in BBOX
        "LAYERS": layer,
        "STYLES": "",
        "SRS": "EPSG:4326",
        "BBOX": f"{bbox.west},{bbox.south},{bbox.east},{bbox.north}",
        "WIDTH": width,
        "HEIGHT": height,
        "FORMAT": "image/jpeg",
        "TIME": date,
    }
    resp = requests.get(GIBS_WMS, params=params, timeout=120)
    resp.raise_for_status()
    if not resp.headers.get("Content-Type", "").startswith("image/"):
        raise RuntimeError(f"GIBS returned {resp.headers.get('Content-Type')}: {resp.text[:200]!r}")
    image = Image.open(io.BytesIO(resp.content)).convert("RGB")
    return image, GeoImage(bbox=bbox, width=image.width, height=image.height)


def load_image(path: Path, bbox: BBox) -> tuple[Image.Image, GeoImage]:
    image = Image.open(path).convert("RGB")
    return image, GeoImage(bbox=bbox, width=image.width, height=image.height)
