"""Settings loaded from the environment (.env). Keys never live in code."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    meta_api_key: str | None
    firms_map_key: str | None
    meta_base_url: str = "https://api.meta.ai/v1"
    sam_model: str = "sam-3.1"
    muse_model: str = "muse-spark-1.3"


def load_settings() -> Settings:
    return Settings(
        meta_api_key=os.getenv("MODEL_API_KEY"),
        firms_map_key=os.getenv("FIRMS_MAP_KEY"),
        meta_base_url=os.getenv("META_BASE_URL", "https://api.meta.ai/v1"),
        sam_model=os.getenv("SAM_MODEL", "sam-3.1"),
        muse_model=os.getenv("MUSE_MODEL", "muse-spark-1.3"),
    )
