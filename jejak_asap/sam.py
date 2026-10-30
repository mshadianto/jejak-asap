"""SAM 3.1 on Meta Model API: point prompts in, smoke masks out.

Prompt and output grammar follow Meta's "Segmenting with prompts" docs; the
official `meta-sam-parser` package builds prompts and decodes masks.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass

import numpy as np
from meta_sam_parser import (
    CompletedOutcome,
    FramePrompt,
    PromptObject,
    PromptPoint,
    SegmentationBoxRecord,
    SegmentationMaskRecord,
    build_object_prompt,
    decode_mask_to_raster,
    image_segmentation_format,
)
from openai import OpenAI
from PIL import Image

MAX_OBJECTS = 16  # API limit for prompted objects


@dataclass
class ObjectSeed:
    object_id: int
    positive: list[tuple[int, int]]
    negative: list[tuple[int, int]]


@dataclass
class SegmentedObject:
    object_id: int
    box: tuple[float, float, float, float]  # left, top, right(excl), bottom(excl) in image pixels
    mask: np.ndarray  # bool, full image size
    confidence: float | None


def build_prompt(seeds: list[ObjectSeed], size: tuple[int, int]) -> str:
    if not seeds:
        raise ValueError("no seeds to prompt")
    if len(seeds) > MAX_OBJECTS:
        raise ValueError(f"SAM 3.1 accepts at most {MAX_OBJECTS} objects, got {len(seeds)}")
    objects = []
    for seed in seeds:
        points = [PromptPoint(x, y) for x, y in seed.positive]
        points += [PromptPoint(x, y, label="negative") for x, y in seed.negative]
        objects.append(PromptObject(id=seed.object_id, prompts=[FramePrompt(points=points)]))
    return build_object_prompt(size=size, objects=objects)


def image_to_data_url(image: Image.Image, quality: int = 92) -> str:
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def call_sam(client: OpenAI, model: str, image: Image.Image, prompt_text: str) -> str:
    """Send one image plus object prompts; return the raw output text."""
    resp = client.responses.create(
        model=model,
        input=[
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt_text},
                    {"type": "input_image", "image_url": image_to_data_url(image)},
                ],
            }
        ],
        metadata={"include_confidence": "true"},
    )
    return resp.output_text or ""


def parse_sam_output(raw: str, width: int, height: int) -> list[SegmentedObject]:
    parser = image_segmentation_format().create_parser()
    parser.push(raw if raw.endswith("\n") else raw + "\n")
    result = parser.finish(CompletedOutcome()).result

    boxes: dict[str, SegmentationBoxRecord] = {}
    masks: dict[str, SegmentationMaskRecord] = {}
    for rec in result.records:
        if isinstance(rec, SegmentationBoxRecord):
            boxes[rec.object_id] = rec
        elif isinstance(rec, SegmentationMaskRecord):
            masks[rec.object_id] = rec

    objects = []
    for oid, mrec in masks.items():
        full = mask_record_to_full(mrec, width, height)
        box = boxes.get(oid)
        bbox = (box.left, box.top, box.right, box.bottom) if box else (
            mrec.bounds.left, mrec.bounds.top, mrec.bounds.right, mrec.bounds.bottom)
        conf = mrec.confidence if mrec.confidence is not None else (box.confidence if box else None)
        try:
            object_id = int(oid)
        except ValueError:
            object_id = -1
        objects.append(SegmentedObject(object_id=object_id, box=bbox, mask=full, confidence=conf))
    return objects


def mask_record_to_full(mrec: SegmentationMaskRecord, width: int, height: int) -> np.ndarray:
    """Decode a mask and place it on the full image grid.

    The decoded raster covers the record's bounds; it may be coarser than the
    image, so it is resized with nearest-neighbour onto the bounds rectangle.
    """
    raster = np.frombuffer(decode_mask_to_raster(mrec.mask), dtype=np.uint8)
    raster = raster.reshape(mrec.mask.height, mrec.mask.width).astype(bool)

    left, top = int(mrec.bounds.left), int(mrec.bounds.top)
    right = min(width, int(round(mrec.bounds.right)))
    bottom = min(height, int(round(mrec.bounds.bottom)))
    full = np.zeros((height, width), dtype=bool)
    if right <= left or bottom <= top:
        return full
    if raster.shape == (height, width):
        return raster  # already full-frame
    patch = Image.fromarray(raster.astype(np.uint8) * 255).resize((right - left, bottom - top), Image.NEAREST)
    full[top:bottom, left:right] = np.asarray(patch) > 127
    return full
