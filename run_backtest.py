"""Score a stage 2 forecast against hourly PM2.5.

    python run_backtest.py --stage2 out/2023-10-13_.../stage2_archive_100m
    python run_backtest.py --stage2 ... --obs-csv sensors.csv   # columns: city,time_utc,pm25
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from jejak_asap.backtest import UNHEALTHY_PM25, load_observations_csv, run_backtest


def plot(result: dict, impact: dict, path: Path, max_cities: int = 6) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    wib = dt.timedelta(hours=7)
    t0 = dt.datetime.fromisoformat(impact["analysis_time_utc"])
    probs = {c["city"]: c["probability_by_hour"] for c in impact["cities"]}
    # Show cities with something to see: observed smoke or a forecast, worst first.
    cities = sorted(result["cities"], key=lambda c: -(c["observed_max_pm25"] or 0))
    cities = [c for c in cities if (c["observed_max_pm25"] or 0) > 20 or c["predicted_probability"] > 0][:max_cities]
    if not cities:
        return
    fig, axes = plt.subplots(len(cities), 1, figsize=(9, 2.1 * len(cities)), sharex=True)
    axes = axes if len(cities) > 1 else [axes]
    for ax, c in zip(axes, cities):
        series = [(dt.datetime.fromisoformat(t) + wib, v) for t, v in result["series"].get(c["city"], []) if v is not None]
        if series:
            ax.plot([t for t, _ in series], [v for _, v in series], color="#7a3e00", lw=2, label="PM2.5 rujukan (µg/m³)")
        ax.axhline(result["pm_threshold_ug_m3"], color="#7a3e00", lw=0.8, ls="--")
        ax.set_ylabel("PM2.5", fontsize=8)
        ax2 = ax.twinx()
        p = probs.get(c["city"], [])
        ax2.fill_between([t0 + wib + dt.timedelta(hours=h) for h in range(len(p))], p, step="mid",
                         color="#e8590c", alpha=0.25, label="peluang asap (prakiraan)")
        ax2.set_ylim(0, 1.05)
        ax2.set_ylabel("peluang", fontsize=8)
        ax.set_title(f"{c['city']}: prakiraan {c['predicted_risk']} ({c['predicted_probability']:.0%}), "
                     f"hasil {c['outcome'].replace('_', ' ')}", fontsize=9, loc="left")
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%d %b %H.%M"))
    axes[-1].set_xlabel("waktu (WIB)")
    h1, l1 = axes[0].get_legend_handles_labels()
    fig.legend(h1 + [plt.Rectangle((0, 0), 1, 1, color="#e8590c", alpha=0.25)], l1 + ["peluang asap (prakiraan)"],
               loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description="Score stage 2 city forecasts against hourly PM2.5")
    ap.add_argument("--stage2", type=Path, required=True, help="stage 2 output folder (with impact.json)")
    ap.add_argument("--obs-csv", type=Path, help="local observations (city,time_utc,pm25) instead of CAMS")
    ap.add_argument("--prob-threshold", type=float, default=0.5, help="forecast probability that counts as a warning")
    ap.add_argument("--pm-threshold", type=float, default=UNHEALTHY_PM25, help="PM2.5 that counts as smoke arrival")
    args = ap.parse_args(argv)

    impact_path = args.stage2 / "impact.json"
    obs = load_observations_csv(args.obs_csv) if args.obs_csv else None
    result = run_backtest(impact_path, obs, args.prob_threshold, args.pm_threshold)
    (args.stage2 / "backtest.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    try:
        plot(result, json.loads(impact_path.read_text(encoding="utf-8")), args.stage2 / "backtest.png")
    except ImportError:
        print("matplotlib not installed: skipped backtest.png")

    s = result["scores"]
    print(f"reference: {result['reference']}; arrival = PM2.5 >= {args.pm_threshold} ug/m3")
    print(f"{'kota':<16}{'prakiraan':<10}{'peluang':>8}{'PM2.5 maks':>12}  hasil")
    for c in result["cities"]:
        pm = "-" if c["observed_max_pm25"] is None else f"{c['observed_max_pm25']:.0f}"
        print(f"{c['city']:<16}{c['predicted_risk']:<10}{c['predicted_probability']:>8.0%}{pm:>12}  {c['outcome']}"
              + ("" if c["arrival_error_h"] is None else f" (selisih tiba {c['arrival_error_h']:+.0f} jam)"))
    print(f"POD {s['pod']}  FAR {s['far']}  CSI {s['csi']}  akurasi {s['accuracy']}  "
          f"galat tiba rata-rata {s['mean_abs_arrival_error_h']} jam ({s['timing_cases']} kasus)")
    print("pembanding tanpa angin (kota dalam radius R km dari api = kena asap):")
    for b in result["distance_baselines"]:
        print(f"  R={b['radius_km']:>4.0f} km  POD {b['pod']}  FAR {b['far']}  CSI {b['csi']}")
    best = max((r for r in result["threshold_sweep"] if r["csi"] is not None), key=lambda r: r["csi"], default=None)
    if best:
        print(f"ambang peluang terbaik di kasus ini: {best['prob_threshold']} (CSI {best['csi']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
