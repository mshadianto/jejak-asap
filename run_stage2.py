"""Jejak Asap stage 2 CLI: where does the smoke go in the next 24 hours, and which cities get it?

Reads a finished stage 1 folder, for example:
    python run_stage2.py --stage1 out/2019-09-14_113.3_-3.6_114.6_-2.5
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import statistics
import sys
from pathlib import Path

import numpy as np

from jejak_asap.geo import BBox
from jejak_asap.imagery import fetch_true_color, load_image
from jejak_asap.impact import CITIES, RiskRules, load_stage1, score_cities, simulate, write_outputs
from jejak_asap.transport import WindGrid, fetch_wind_grid


def start_time(stage1: Path, date: str) -> dt.datetime:
    """Analysis time = the satellite pass, i.e. the median hotspot hour (UTC)."""
    hours = []
    path = stage1 / "hotspots.csv"
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                t = str(row.get("acq_time", "")).zfill(4)
                if t[:2].isdigit() and row.get("daynight", "D") != "N":
                    hours.append(int(t[:2]))
    hour = int(statistics.median(hours)) if hours else 6
    return dt.datetime.fromisoformat(date).replace(hour=hour)


def save_grid(path: Path, g: WindGrid) -> None:
    np.savez_compressed(path, lons=g.lons, lats=g.lats, u=g.u, v=g.v,
                        times=np.array([t.isoformat() for t in g.times]))


def load_grid(path: Path) -> WindGrid:
    d = np.load(path)
    return WindGrid(d["lons"], d["lats"], [dt.datetime.fromisoformat(str(t)) for t in d["times"]], d["u"], d["v"])


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description="Smoke trajectories and city impact from a stage 1 result")
    ap.add_argument("--stage1", type=Path, required=True, help="stage 1 output folder")
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--pad", type=float, default=2.5, help="degrees added around the stage 1 box for the wind domain")
    ap.add_argument("--wind-level", default="100m", help="Open-Meteo height suffix: 10m, 80m, 100m, 120m")
    ap.add_argument("--wind-source", choices=["archive", "forecast"], default="archive",
                    help="archive = ERA5 reanalysis (backtests); forecast = live forecast")
    ap.add_argument("--members", type=int, default=20, help="wind scenarios")
    ap.add_argument("--radius-km", type=float, default=25.0, help="distance from a city centre that counts as reached")
    ap.add_argument("--map-width", type=int, default=1400)
    ap.add_argument("--resume", action="store_true", help="reuse saved wind grid and base map")
    args = ap.parse_args(argv)

    report = json.loads((args.stage1 / "report.json").read_text(encoding="utf-8"))
    date = report["date"]
    b = report["bbox"]
    stage_box = BBox(b["west"], b["south"], b["east"], b["north"])
    domain = BBox(stage_box.west - args.pad, stage_box.south - args.pad,
                  stage_box.east + args.pad, stage_box.north + args.pad)
    out_dir = args.stage1 / f"stage2_{args.wind_source}_{args.wind_level}"
    out_dir.mkdir(parents=True, exist_ok=True)

    plumes, fires, _ = load_stage1(args.stage1 / "plumes.geojson")
    print(f"sources: {len(plumes)} accepted plumes, {len(fires)} fire clusters")
    if not plumes and not fires:
        print("nothing to transport: stage 1 accepted no plumes and found no fires", file=sys.stderr)
        return 2

    t0 = start_time(args.stage1, date)
    grid_path = out_dir / "wind_grid.npz"
    if args.resume and grid_path.exists():
        grid = load_grid(grid_path)
        print("reusing saved wind grid")
    else:
        print(f"fetching {args.wind_level} wind ({args.wind_source}) from {t0:%Y-%m-%d %H:00} UTC for {args.hours} h ...")
        grid = fetch_wind_grid(domain, t0, args.hours, level=args.wind_level, source=args.wind_source)
        save_grid(grid_path, grid)
    print(f"wind grid: {len(grid.lons)} x {len(grid.lats)} points, {len(grid.times)} hours")

    base_path = out_dir / "basemap.jpg"
    if args.resume and base_path.exists():
        base, geo = load_image(base_path, domain)
    else:
        print("fetching base map from NASA GIBS ...")
        base, geo = fetch_true_color(domain, date, width=args.map_width)
        base.save(base_path, quality=90)

    sim = simulate(grid, plumes, fires, hours=min(args.hours, len(grid.times) - 1), members=args.members)
    rules = RiskRules(radius_km=args.radius_km)
    impacts = score_cities(sim, CITIES, domain, fires, rules)
    meta = {"date": date, "analysis_time_utc": t0.isoformat(), "hours": len(sim.times) - 1,
            "wind_source": args.wind_source, "wind_level": args.wind_level, "members": args.members,
            "domain": vars(domain), "rules": vars(rules),
            "note": "Direction and timing of smoke only; not a PM2.5 concentration forecast. "
                    "Risk thresholds are uncalibrated placeholders until backtested."}
    write_outputs(out_dir, sim, impacts, base, geo, plumes, meta)

    print(f"{'kota':<16}{'risiko':<8}{'peluang':>8}  tiba (>=50% skenario)   jarak api")
    for c in impacts:
        print(f"{c.city:<16}{c.risk:<8}{c.max_probability:>8.0%}  {c.likely_arrival_wib or '-':<22}{c.distance_to_nearest_fire_km:>7.0f} km")
    print(f"done -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
