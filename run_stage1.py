"""Jejak Asap stage 1 CLI.

Example (14 Sep 2019, Central Kalimantan south coast):
    python run_stage1.py --date 2019-09-14 --bbox 113.3,-3.6,114.6,-2.5

Long runs can be split: add --resume --time-budget 35 and repeat the same command
until it prints "done" (exit code 3 means "not finished yet, run again").
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from openai import OpenAI

from jejak_asap.config import load_settings
from jejak_asap.firms import DEFAULT_SOURCE, Hotspot, fetch_hotspots, hotspots_from_gibs, load_hotspots_csv
from jejak_asap.geo import BBox
from jejak_asap.imagery import fetch_true_color, load_image
from jejak_asap.pipeline import IncompleteRun, dominant_hour, run_stage1
from jejak_asap.wind import Wind, fetch_wind

EXIT_INCOMPLETE = 3


def save_hotspots(path: Path, hotspots: list[Hotspot]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["latitude", "longitude", "frp", "confidence", "acq_date", "acq_time", "daynight"])
        for h in hotspots:
            w.writerow([h.lat, h.lon, h.frp, h.confidence, h.acq_date, h.acq_time, h.daynight])


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(line_buffering=True)  # progress shows up live in logs
    started = time.monotonic()
    ap = argparse.ArgumentParser(description="Fire hotspots -> SAM 3.1 smoke plumes -> Muse check")
    ap.add_argument("--date", required=True, help="YYYY-MM-DD (UTC)")
    ap.add_argument("--bbox", required=True, help="west,south,east,north in degrees")
    ap.add_argument("--out", default="out", help="output folder")
    ap.add_argument("--width", type=int, default=1600, help="image width in pixels")
    ap.add_argument("--firms-source", default=DEFAULT_SOURCE, help="e.g. VIIRS_SNPP_SP (archive) or VIIRS_SNPP_NRT")
    ap.add_argument("--hotspots-csv", type=Path, help="use a local FIRMS CSV instead of the API")
    ap.add_argument("--image", type=Path, help="use a local image that covers exactly --bbox")
    ap.add_argument("--offset-km", type=float, default=6.0, help="seed distance downwind of each fire (0 = on the fire)")
    ap.add_argument("--no-wind", action="store_true", help="skip Open-Meteo; seeds go on the fires")
    ap.add_argument("--wind-from", type=float, help="manual wind direction in degrees (blowing FROM), skips Open-Meteo")
    ap.add_argument("--wind-kmh", type=float, default=10.0, help="manual wind speed, used with --wind-from")
    ap.add_argument("--no-muse", action="store_true", help="skip the Muse smoke/cloud check")
    ap.add_argument("--dry-run", action="store_true", help="build prompts and overlay without calling Meta")
    ap.add_argument("--reuse-sam", action="store_true", help="reuse saved SAM batches (sam_raw_NN.txt) when their prompts are unchanged")
    ap.add_argument("--resume", action="store_true",
                    help="reuse everything already saved in the output folder (image, hotspots, wind, SAM, masks, Muse)")
    ap.add_argument("--time-budget", type=float, help="seconds; stop cleanly between stages and exit 3 if exceeded")
    args = ap.parse_args(argv)

    settings = load_settings()
    bbox = BBox.parse(args.bbox)
    out_dir = Path(args.out) / f"{args.date}_{args.bbox.replace(',', '_')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    deadline = started + args.time_budget if args.time_budget else None

    image_path, hotspots_path, wind_path = out_dir / "image.jpg", out_dir / "hotspots.csv", out_dir / "wind.json"

    if args.image:
        image, geo = load_image(args.image, bbox)
    elif args.resume and image_path.exists():
        image, geo = load_image(image_path, bbox)
        print("reusing saved image")
    else:
        print("fetching true-colour image from NASA GIBS ...")
        image, geo = fetch_true_color(bbox, args.date, width=args.width)
        image.save(image_path, quality=95)
        image, geo = load_image(image_path, bbox)  # SAM sees exactly the saved file

    if args.hotspots_csv:
        hotspots = load_hotspots_csv(args.hotspots_csv)
    elif args.resume and hotspots_path.exists():
        hotspots = load_hotspots_csv(hotspots_path)
        print(f"reusing {len(hotspots)} saved hotspots")
    elif settings.firms_map_key:
        print("fetching hotspots from NASA FIRMS ...")
        hotspots = fetch_hotspots(settings.firms_map_key, bbox, args.date, source=args.firms_source)
        save_hotspots(hotspots_path, hotspots)
    else:
        print("FIRMS_MAP_KEY is not set: reading approximate hotspots from the GIBS fire layer "
              "(positions only, no FRP or times).")
        hotspots = hotspots_from_gibs(bbox, args.date, geo.width, geo.height)
        save_hotspots(hotspots_path, hotspots)

    wind = None
    if args.wind_from is not None:
        wind = Wind(speed_kmh=args.wind_kmh, direction_from_deg=args.wind_from % 360)
        print(f"manual wind: from {wind.direction_from_deg:.0f} deg -> smoke drifts toward {wind.bearing_to_deg:.0f} deg")
    elif args.resume and wind_path.exists():
        data = json.loads(wind_path.read_text(encoding="utf-8"))
        wind = Wind(**data) if data else None
        print("reusing saved wind")
    elif not args.no_wind and hotspots:
        hour = dominant_hour(hotspots)
        lon, lat = (bbox.west + bbox.east) / 2, (bbox.south + bbox.north) / 2
        try:
            wind = fetch_wind(lon, lat, args.date, hour)
            print(f"wind at {hour:02d} UTC: from {wind.direction_from_deg:.0f} deg, {wind.speed_kmh:.1f} km/h "
                  f"-> smoke drifts toward {wind.bearing_to_deg:.0f} deg")
            wind_path.write_text(json.dumps(asdict(wind)), encoding="utf-8")
        except Exception as exc:  # wind is optional for stage 1
            print(f"wind unavailable ({exc}); seeds go on the fires")

    client = None
    if not args.dry_run:
        if not settings.meta_api_key:
            print("MODEL_API_KEY is not set; running as --dry-run", file=sys.stderr)
        else:
            client = OpenAI(base_url=settings.meta_base_url, api_key=settings.meta_api_key,
                            timeout=90, max_retries=1)

    try:
        report = run_stage1(
            image=image, geo=geo, hotspots=hotspots, date=args.date, out_dir=out_dir, client=client,
            sam_model=settings.sam_model, muse_model=settings.muse_model, wind=wind,
            offset_km=args.offset_km, use_muse=not args.no_muse,
            reuse_sam=args.reuse_sam or args.resume, deadline=deadline,
        )
    except IncompleteRun as exc:
        print(f"not finished: {exc}. Run the same command again with --resume.")
        return EXIT_INCOMPLETE
    print(f"done: {report['accepted_plumes']} accepted plumes, {report['accepted_area_km2']} km2 -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
