"""Multi-day backtest of the smoke transport, fires only (no SAM/Muse), against hourly PM2.5.

Each date: FIRMS hotspots over the whole domain -> fire clusters -> 12 h spin-up plus
24 h of particle transport with ERA5 wind -> city scores -> compare with CAMS PM2.5 and
with no-wind distance rules. Results are cached per date, so the command can be
repeated (with --time-budget) until every date is done.

    python run_series.py --start 2023-09-01 --end 2023-10-31 --step-days 2
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from jejak_asap.backtest import UNHEALTHY_PM25, fetch_pm25, judge, scores
from jejak_asap.config import load_settings
from jejak_asap.firms import cluster_hotspots, fetch_hotspots, filter_hotspots
from jejak_asap.geo import BBox
from jejak_asap.impact import CITIES, FireSource, SimulationResult, load_stage1, score_cities, simulate
from jejak_asap.pipeline import dominant_hour
from jejak_asap.transport import fetch_wind_grid

DEFAULT_DOMAIN = "110.5,-4.5,117.5,0.5"   # Central and South Kalimantan
RADII = (25, 50, 75, 100, 150)
THRESHOLDS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)


def find_plumes(root: Path, date: str, domain: BBox) -> list[Path]:
    """plumes.geojson of every stage 1 run for this date under `root`: one regional run over the
    domain, or several local boxes (folder names start with the date)."""
    return sorted(p for p in root.glob(f"{date}_*/plumes.geojson"))


def run_day(date: str, domain: BBox, firms_key: str, source: str, members: int, spinup_h: int,
            plumes_geojson: list[Path] | None = None) -> dict:
    hotspots = fetch_hotspots(firms_key, domain, date, source=source)
    usable = filter_hotspots(hotspots, domain, daytime_only=True)
    clusters = cluster_hotspots(usable, cell_deg=0.1, max_clusters=200, min_hotspots=2)
    fires = [FireSource(c.cluster_id, *c.centroid, c.frp_sum) for c in clusters]
    t0 = dt.datetime.fromisoformat(date).replace(hour=dominant_hour(usable) if usable else 6)
    cities = {k: v for k, v in CITIES.items() if domain.contains(*v)}

    plumes = []
    for path in plumes_geojson or []:
        found, _, _ = load_stage1(path)
        plumes += found
    if fires:
        grid = fetch_wind_grid(domain, t0 - dt.timedelta(hours=spinup_h), 24 + spinup_h)
        # Plumes seen by SAM + Muse enter at the analysis hour, after the fires-only spin-up.
        full = simulate(grid, plumes, fires, hours=24 + spinup_h, members=members,
                        initial_per_member=300 if plumes else 0, plume_at_h=spinup_h)
        sim = SimulationResult(full.times[spinup_h:], full.snapshots[spinup_h:], full.members)
        impacts = [asdict(c) for c in score_cities(sim, cities, domain, fires)]
    else:
        impacts = [{"city": n, "lon": lo, "lat": la, "risk": "AMAN", "max_probability": 0.0,
                    "likely_arrival_utc": None, "distance_to_nearest_fire_km": float("inf"),
                    "probability_by_hour": [0.0] * 25} for n, (lo, la) in cities.items()]
    obs = fetch_pm25({c["city"]: (c["lon"], c["lat"]) for c in impacts}, t0, 24)
    return {"date": date, "analysis_time_utc": t0.isoformat(), "hotspots": len(hotspots),
            "plumes": len(plumes), "plume_area_km2": round(sum(pl.area_km2 for pl in plumes), 1),
            "usable_hotspots": len(usable), "fire_clusters": len(fires), "cities": impacts,
            "observations": {k: [[t.isoformat(), v] for t, v in s] for k, s in obs.items()}}


def _contingency(pairs) -> dict:
    """pairs of (warned, observed) booleans -> POD, FAR, CSI."""
    a = sum(w and o for w, o in pairs)
    b = sum(w and not o for w, o in pairs)
    c = sum(o and not w for w, o in pairs)
    return {"hit": a, "false_alarm": b, "miss": c,
            "pod": None if a + c == 0 else round(a / (a + c), 2),
            "far": None if a + b == 0 else round(b / (a + b), 2),
            "csi": None if a + b + c == 0 else round(a / (a + b + c), 2)}


def city_day_rows(days: list[dict], pm_threshold: float) -> list[list[dict]]:
    """Per day, one row per city with data: forecast fields plus observed smoke at start and in 24 h."""
    out = []
    for d in days:
        rows = []
        for c in d["cities"]:
            vals = [v for _, v in d["observations"].get(c["city"], []) if v is not None]
            if not vals:
                continue
            rows.append({"city": c["city"], "p": c["max_probability"], "dist": c.get("distance_to_nearest_fire_km") or 1e9,
                         "smoky_now": vals[0] >= pm_threshold, "smoky": max(vals) >= pm_threshold})
        out.append(rows)
    return out


RULES = {
    "persistence": lambda r, k: r["smoky_now"],
    "model": lambda r, k: r["p"] >= k,
    "distance": lambda r, k: r["dist"] <= k,
    "persistence+model": lambda r, k: r["smoky_now"] or r["p"] >= k,
    "persistence+distance": lambda r, k: r["smoky_now"] or r["dist"] <= k,
}


def _csi(rows, rule, k) -> float:
    return _contingency([(RULES[rule](r, k), r["smoky"]) for r in rows])["csi"] or 0.0


def bootstrap_diff(by_day, rule_a, k_a, rule_b, k_b, n=2000, seed=1) -> dict:
    """CSI(a) - CSI(b), resampling whole days (city-days on one day are not independent)."""
    import random
    rng = random.Random(seed)
    diffs = []
    for _ in range(n):
        sample = [r for d in rng.choices(by_day, k=len(by_day)) for r in d]
        diffs.append(_csi(sample, rule_a, k_a) - _csi(sample, rule_b, k_b))
    diffs.sort()
    return {"a": f"{rule_a} {k_a}", "b": f"{rule_b} {k_b}", "mean": round(sum(diffs) / n, 3),
            "ci95": [round(diffs[int(0.025 * n)], 3), round(diffs[int(0.975 * n) - 1], 3)],
            "p_a_better": round(sum(x > 0 for x in diffs) / n, 2)}


def evaluate(days: list[dict], pm_threshold: float, n_boot: int = 2000) -> dict:
    """Pool every city-day; score the model at each threshold against no-wind and persistence rules."""
    def obs_of(day, name):
        return [(dt.datetime.fromisoformat(t), v) for t, v in day["observations"].get(name, [])]

    model = {}
    for th in THRESHOLDS:
        vs = [judge(c, obs_of(d, c["city"]), th, pm_threshold) for d in days for c in d["cities"]]
        model[th] = scores(vs)
    by_day = city_day_rows(days, pm_threshold)
    rows = [r for d in by_day for r in d]
    base = {r: _contingency([(RULES["distance"](x, r), x["smoky"]) for x in rows]) for r in RADII}
    best_th = max(THRESHOLDS, key=lambda t: model[t]["csi"] or 0)
    best_r = max(RADII, key=lambda r: base[r]["csi"] or 0)
    combined = {
        "persistence": _contingency([(x["smoky_now"], x["smoky"]) for x in rows]),
        **{f"persistence+model {t}": _contingency([(RULES["persistence+model"](x, t), x["smoky"]) for x in rows])
           for t in THRESHOLDS},
        **{f"persistence+distance {r}": _contingency([(RULES["persistence+distance"](x, r), x["smoky"]) for x in rows])
           for r in RADII},
    }
    best_pm = max(THRESHOLDS, key=lambda t: combined[f"persistence+model {t}"]["csi"] or 0)
    best_pd = max(RADII, key=lambda r: combined[f"persistence+distance {r}"]["csi"] or 0)
    clean = [x for x in rows if not x["smoky_now"]]
    new_arrivals = {
        "city_days": len(clean), "became_smoky": sum(x["smoky"] for x in clean),
        "model": {str(t): _contingency([(RULES["model"](x, t), x["smoky"]) for x in clean]) for t in THRESHOLDS},
        "distance": {str(r): _contingency([(RULES["distance"](x, r), x["smoky"]) for x in clean]) for r in RADII},
    }
    # Thresholds above are picked on the same data, so these intervals are, if anything, generous.
    tests = [bootstrap_diff(by_day, "model", best_th, "distance", best_r, n_boot),
             bootstrap_diff(by_day, "persistence+model", best_pm, "persistence+distance", best_pd, n_boot),
             bootstrap_diff(by_day, "persistence+model", best_pm, "persistence", 0, n_boot)]
    # Honest check: pick each rule's setting on the first half of the dates, score it on the second half.
    half = len(by_day) // 2
    tune, test = [r for d in by_day[:half] for r in d], [r for d in by_day[half:] for r in d]
    dates = [d.get("date", "") for d in days]
    holdout = {"tune_dates": [dates[0], dates[half - 1]] if half else [],
               "test_dates": [dates[half], dates[-1]] if len(dates) > half else []}
    for rule, ks in (("model", THRESHOLDS), ("distance", RADII), ("persistence", (0,)),
                     ("persistence+model", THRESHOLDS), ("persistence+distance", RADII)):
        k = max(ks, key=lambda k: _contingency([(RULES[rule](r, k), r["smoky"]) for r in tune])["csi"] or 0)
        holdout[rule] = {"setting": k, **_contingency([(RULES[rule](r, k), r["smoky"]) for r in test])}
    return {"model": {str(k): v for k, v in model.items()}, "holdout": holdout,
            "baselines": {str(k): v for k, v in base.items()},
            "combined": combined, "new_arrivals": new_arrivals, "bootstrap": tests,
            "best_threshold": best_th, "best_radius_km": best_r,
            "best_persistence_model_threshold": best_pm, "best_persistence_distance_radius_km": best_pd,
            "city_days": sum(len(d["cities"]) for d in days), "days": len(days),
            "smoky_city_days": sum(x["smoky"] for x in rows)}


def plot(summary: dict, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(11, 4.2), gridspec_kw={"width_ratios": [1.3, 1]})
    ths = [float(t) for t in summary["model"]]
    ax.plot(ths, [summary["model"][str(t)]["csi"] or 0 for t in ths], marker="o", color="#e8590c",
            lw=2, label="Jejak Asap (angin + partikel)")
    base = {r: s["csi"] or 0 for r, s in summary["baselines"].items()}
    best = str(summary["best_radius_km"])
    ax.axhline(base[best], ls="--", lw=1.2, color="#555", label=f"tanpa angin: radius {best} km (terbaik)")
    ax.axhspan(min(base.values()), max(base.values()), color="#ccc", alpha=0.4,
               label=f"tanpa angin: radius {min(RADII)}-{max(RADII)} km")
    ax.set_xlabel("ambang peluang untuk peringatan")
    ax.set_ylabel("CSI (1 = sempurna)")
    ax.set_title("A. Hanya dari titik api", fontsize=10, loc="left")
    ax.set_xlim(0.05, 0.95)
    ax.set_ylim(0, 0.5)
    ax.legend(loc="upper right", fontsize=8)

    c = summary["combined"]
    bars = [("PM2.5 saat ini\nsaja", c["persistence"]),
            (f"saat ini +\nradius {summary['best_persistence_distance_radius_km']} km",
             c[f"persistence+distance {summary['best_persistence_distance_radius_km']}"]),
            (f"saat ini +\nJejak Asap", c[f"persistence+model {summary['best_persistence_model_threshold']}"])]
    colors = ["#999", "#bbb", "#e8590c"]
    ax2.bar(range(3), [s["csi"] or 0 for _, s in bars], color=colors)
    for i, (_, s) in enumerate(bars):
        ax2.text(i, (s["csi"] or 0) + 0.01, f"CSI {s['csi']}\nPOD {s['pod']} FAR {s['far']}", ha="center", fontsize=7)
    ax2.set_xticks(range(3), [n for n, _ in bars], fontsize=8)
    ax2.set_ylim(0, 0.5)
    ax2.set_title("B. Digabung dengan PM2.5 jam awal", fontsize=10, loc="left")
    fig.suptitle(f"Backtest {summary['days']} hari (Sep-Okt 2023, Kalteng-Kalsel), {summary['city_days']} kota-hari, "
                 f"{summary['smoky_city_days']} berasap; rujukan PM2.5 CAMS", fontsize=10, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(line_buffering=True)
    started = time.monotonic()
    ap = argparse.ArgumentParser(description="Multi-day fires-only backtest against hourly PM2.5")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--step-days", type=int, default=2)
    ap.add_argument("--domain", default=DEFAULT_DOMAIN)
    ap.add_argument("--out", type=Path, default=Path("out/series"))
    ap.add_argument("--firms-source", default="VIIRS_SNPP_SP")
    ap.add_argument("--members", type=int, default=20)
    ap.add_argument("--spinup-h", type=int, default=12)
    ap.add_argument("--pm-threshold", type=float, default=UNHEALTHY_PM25)
    ap.add_argument("--plumes-root", type=Path,
                    help="folder of stage 1 runs over this domain; only dates with plumes are run, plumes added")
    ap.add_argument("--dates", help="comma-separated dates instead of --start/--end")
    ap.add_argument("--time-budget", type=float, help="seconds; stop between dates and exit 3 if exceeded")
    args = ap.parse_args(argv)

    key = load_settings().firms_map_key
    domain = BBox.parse(args.domain)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.dates:
        dates = [x.strip() for x in args.dates.split(",") if x.strip()]
    else:
        d, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
        dates = []
        while d <= end:
            dates.append(d.isoformat())
            d += dt.timedelta(days=args.step_days)
    if args.plumes_root:
        with_plumes = sum(bool(find_plumes(args.plumes_root, x, domain)) for x in dates)
        print(f"{with_plumes}/{len(dates)} dates have stage 1 runs; the rest count as no plumes seen")

    days = []
    for date in dates:
        path = args.out / f"{date}.json"
        if path.exists():
            days.append(json.loads(path.read_text(encoding="utf-8")))
            continue
        if not key:
            print("FIRMS_MAP_KEY is required for dates not yet cached", file=sys.stderr)
            return 2
        if args.time_budget and time.monotonic() - started > args.time_budget:
            print(f"not finished: {len(days)}/{len(dates)} dates done. Run again to continue.")
            return 3
        try:
            day = run_day(date, domain, key, args.firms_source, args.members, args.spinup_h,
                          find_plumes(args.plumes_root, date, domain) if args.plumes_root else None)
        except Exception as exc:  # keep going; a failed date is retried next run
            print(f"{date}: failed ({exc})")
            continue
        path.write_text(json.dumps(day, default=str), encoding="utf-8")
        days.append(day)
        smoky = sum(1 for c in day["cities"] if max((v or 0) for _, v in day["observations"].get(c["city"], [[0, 0]])) >= args.pm_threshold)
        print(f"{date}: {day['fire_clusters']} fire clusters, {smoky}/{len(day['cities'])} cities smoky")

    if len(days) < len(dates):
        print(f"{len(dates) - len(days)} dates failed; run again to retry them")
    summary = evaluate(days, args.pm_threshold)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    try:
        plot(summary, args.out / "summary.png")
    except ImportError:
        pass
    print(f"\n{summary['days']} days, {summary['city_days']} city-days, {summary['smoky_city_days']} smoky")
    print("Jejak Asap by probability threshold:")
    for t, s in summary["model"].items():
        print(f"  >= {float(t):.1f}: POD {s['pod']}  FAR {s['far']}  CSI {s['csi']}  "
              f"timing error {s['mean_abs_arrival_error_h']} h ({s['timing_cases']} cases)")
    print("No-wind distance rules:")
    for r, s in summary["baselines"].items():
        print(f"  <= {r:>3} km: POD {s['pod']}  FAR {s['far']}  CSI {s['csi']}")
    print(f"best threshold {summary['best_threshold']}, best radius {summary['best_radius_km']} km")
    c = summary["combined"]
    print("With the PM2.5 observed at the start (operational systems have it):")
    for name in ("persistence", f"persistence+distance {summary['best_persistence_distance_radius_km']}",
                 f"persistence+model {summary['best_persistence_model_threshold']}"):
        s = c[name]
        print(f"  {name:<28} POD {s['pod']}  FAR {s['far']}  CSI {s['csi']}")
    na = summary["new_arrivals"]
    print(f"New arrivals only ({na['city_days']} city-days clean at start, {na['became_smoky']} became smoky): "
          f"model 0.5 CSI {na['model']['0.5']['csi']}, distance 50 km CSI {na['distance']['50']['csi']}")
    h = summary["holdout"]
    print(f"Holdout (settings picked on {h['tune_dates'][0]}..{h['tune_dates'][-1]}, "
          f"scored on {h['test_dates'][0]}..{h['test_dates'][-1]}):")
    for rule in ("model", "distance", "persistence", "persistence+model", "persistence+distance"):
        s = h[rule]
        print(f"  {rule:<22} setting {s['setting']:<4} POD {s['pod']}  FAR {s['far']}  CSI {s['csi']}")
    print("Bootstrap over days (CSI a - b):")
    for t in summary["bootstrap"]:
        print(f"  {t['a']} vs {t['b']}: {t['mean']:+.3f}  95% CI {t['ci95']}  P(a better) {t['p_a_better']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
