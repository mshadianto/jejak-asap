"""Muse as a visual QA gate: is each SAM mask smoke, cloud, or something else?"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import cv2
import numpy as np
from openai import OpenAI
from PIL import Image

from .sam import SegmentedObject, image_to_data_url

LABELS = {"smoke", "cloud", "mixed", "other"}

CHECK_PROMPT = """You are a remote-sensing analyst checking an automatic segmentation.
The image is a true-colour satellite crop from Indonesia. The RED outline marks one region found by a segmentation model.

Decide what the outlined region mostly contains:
- "smoke": grey or grey-brown, semi-transparent, streaked downwind from a source
- "cloud": bright white, opaque, puffy or layered with sharp edges
- "mixed": a substantial share of both smoke and cloud
- "other": land, water, burn scar or anything else

Base the answer only on what is visible. Return JSON only:
{"label": "smoke|cloud|mixed|other", "smoke_fraction": 0.0-1.0, "confidence": 0.0-1.0, "reason": "one sentence"}"""


@dataclass
class MuseVerdict:
    label: str
    smoke_fraction: float | None
    confidence: float | None
    reason: str
    raw: str


def crop_with_outline(image: Image.Image, obj: SegmentedObject, pad: int = 40, max_side: int = 1024) -> Image.Image:
    left, top, right, bottom = (int(v) for v in obj.box)
    left, top = max(0, left - pad), max(0, top - pad)
    right, bottom = min(image.width, right + pad), min(image.height, bottom + pad)
    crop = np.array(image.crop((left, top, right, bottom)))
    sub = obj.mask[top:bottom, left:right].astype(np.uint8)
    contours, _ = cv2.findContours(sub, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    thickness = max(2, round(max(crop.shape[:2]) / 400))
    cv2.drawContours(crop, contours, -1, (255, 0, 0), thickness)
    out = Image.fromarray(crop)
    out.thumbnail((max_side, max_side))
    return out


def parse_verdict(text: str) -> MuseVerdict:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return MuseVerdict("other", None, None, "unparseable response", text)
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return MuseVerdict("other", None, None, "invalid JSON", text)
    label = str(data.get("label", "other")).lower()
    if label not in LABELS:
        label = "other"

    def num(key: str) -> float | None:
        try:
            return max(0.0, min(1.0, float(data[key])))
        except (KeyError, TypeError, ValueError):
            return None

    return MuseVerdict(label, num("smoke_fraction"), num("confidence"), str(data.get("reason", "")), text)


def check_object(client: OpenAI, model: str, image: Image.Image, obj: SegmentedObject) -> MuseVerdict:
    crop = crop_with_outline(image, obj)
    resp = client.responses.create(
        model=model,
        temperature=0.2,
        input=[
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": CHECK_PROMPT},
                    {"type": "input_image", "image_url": image_to_data_url(crop)},
                ],
            }
        ],
    )
    return parse_verdict(resp.output_text or "")


def is_smoke(verdict: MuseVerdict, min_fraction: float = 0.5) -> bool:
    if verdict.label == "smoke":
        return True
    if verdict.label == "mixed":
        return (verdict.smoke_fraction or 0.0) >= min_fraction
    return False
