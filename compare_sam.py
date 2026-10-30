"""Paired test: does adding the SAM 3.1 + Muse smoke plumes improve the fires-only forecast?

Both runs use the same dates, wind, fires, ensemble and random seed; the only difference is
the plumes SAM found and Muse accepted, released at the analysis hour.

    python run_series.py --dates ... --plumes-root out/plumes --out out/series_sam
    python compare_sam.py --fires out/series --sam out/series_sam
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

from jejak_asap.backtest import UNHEALTHY_PM25
from run_series import THRESHOLDS, _contingency, city_day_rows


def load(folder: Path, dates: list[str]) -> list[dict]:
    return [json.loads((folder / f"{d}.json").read_text(encoding="utf-8")) for d in dates]


def csi_at(by_day: list[list[dict]], th: float, persistence: bool = False) -> dict:
    rows = [r for d in by_day for r in d]
    return _contingency([((persistence and r["smoky_now"]) or r["p"] >= th, r["smoky"]) for r in rows])


def paired_bootstrap(a: list[list[dict]], b: list[list[dict]], th: float, n: int = 2000, seed: int = 1,
                     persistence: bool = False) -> dict:
    rng = random.Random(seed)
    idx = list(range(len(a)))
    diffs = []
    for _ in range(n):
        pick = rng.choices(idx, k=len(idx))
        sa = csi_at([a[i] for i in pick], th, persistence)["csi"] or 0.0
        sb = csi_at([b[i] for i in pick], th, persistence)["csi"] or 0.0
        diffs.append(sa - sb)
    diffs.sort()
    return {"mean": round(sum(diffs) / n, 3), "ci95": [round(diffs[int(0.025 * n)], 3), round(diffs[int(0.975 * n) - 1], 3)],
            "p_sam_better": round(sum(x > 0 for x in diffs) / n, 2)}


def plot(result: dict, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(12, 4.6), gridspec_kw={"width_ratios": [1, 1.6]})
    ths = [float(t) for t in result["fires_only"]]
    ax.plot(ths, [result["fires_only"][str(t)]["csi"] or 0 for t in ths], marker="o", color="#888", lw=2,
            label="titik api saja")
    ax.plot(ths, [result["with_sam"][str(t)]["csi"] or 0 for t in ths], marker="o", color="#e8590c", lw=2,
            label="titik api + asap SAM/Muse")
    ax.set_xlabel("ambang peluang untuk peringatan")
    ax.set_ylabel("CSI (1 = sempurna)")
    ax.set_ylim(0, 1)
    ax.set_xlim(0.05, 0.95)
    ax.legend(loc="lower left", fontsize=8)
    ax.set_title("A. Semua kota-hari", fontsize=10, loc="left")

    days = result["per_day"]
    x = range(len(days))
    w = 0.38
    ax2.bar([i - w / 2 for i in x], [d["fires_only_csi"] or 0 for d in days], w, color="#999", label="titik api saja")
    ax2.bar([i + w / 2 for i in x], [d["with_sam_csi"] or 0 for d in days], w, color="#e8590c", label="+ asap SAM/Muse")
    for i, d in enumerate(days):
        if d["plumes"]:
            ax2.plot(i, 1.03, marker="v", color="#e8590c", ms=4)
    ax2.set_xticks(list(x), [f"{d['date'][8:]}/{d['date'][5:7]} ({d['smoky']})" for d in days],
                   fontsize=6, rotation=90)
    ax2.set_ylim(0, 1.1)
    ax2.set_ylabel(f"CSI pada ambang {result['threshold']}")
    ax2.set_title("B. Per tanggal; (n) = kota berasap, ▼ = ada asap diterima Muse", fontsize=9, loc="left")
    ax2.legend(loc="upper left", fontsize=7)
    fig.suptitle(f"{result.get('title', 'Uji berpasangan')} {len(days)} tanggal 2023: apakah asap yang dilihat SAM 3.1 + Muse memperbaiki prakiraan?",
                 fontsize=10, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description="Fires-only vs fires + SAM/Muse plumes, same dates")
    ap.add_argument("--fires", type=Path, default=Path("out/series"))
    ap.add_argument("--sam", type=Path, default=Path("out/series_sam"))
    ap.add_argument("--threshold", type=float, default=0.5, help="frozen from the fires-only series")
    ap.add_argument("--pm-threshold", type=float, default=UNHEALTHY_PM25)
    ap.add_argument("--cities", help="comma-separated: score only these cities (e.g. those inside local SAM boxes)")
    ap.add_argument("--title", default="Uji berpasangan", help="chart title prefix")
    args = ap.parse_args(argv)

    dates = sorted(p.stem for p in args.sam.glob("2*.json") if (args.fires / p.name).exists())
    if not dates:
        print("no paired dates found", file=sys.stderr)
        return 2
    fo, sam = load(args.fires, dates), load(args.sam, dates)
    fo_rows, sam_rows = city_day_rows(fo, args.pm_threshold), city_day_rows(sam, args.pm_threshold)
    if args.cities:
        keep = {c.strip() for c in args.cities.split(",")}
        fo_rows = [[r for r in d if r["city"] in keep] for d in fo_rows]
        sam_rows = [[r for r in d if r["city"] in keep] for d in sam_rows]
    th = args.threshold
    per_day = []
    for d, a, b, s in zip(dates, fo_rows, sam_rows, sam):
        per_day.append({"date": d, "cities": len(a), "smoky": sum(r["smoky"] for r in a),
                        "plumes": s.get("plumes"), "plume_area_km2": s.get("plume_area_km2"),
                        "fires_only_csi": csi_at([a], th)["csi"], "with_sam_csi": csi_at([b], th)["csi"]})
    result = {
        "dates": dates, "threshold": th, "title": args.title, "cities": args.cities,
        "fires_only": {str(t): csi_at(fo_rows, t) for t in THRESHOLDS},
        "with_sam": {str(t): csi_at(sam_rows, t) for t in THRESHOLDS},
        "with_persistence": {"fires_only": csi_at(fo_rows, 0.9, True), "with_sam": csi_at(sam_rows, 0.9, True)},
        "bootstrap": paired_bootstrap(sam_rows, fo_rows, th),
        "bootstrap_with_persistence": paired_bootstrap(sam_rows, fo_rows, 0.9, persistence=True),
        "per_day": per_day,
    }
    args.sam.mkdir(parents=True, exist_ok=True)
    (args.sam / "compare.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    try:
        plot(result, args.sam / "compare.png")
    except ImportError:
        pass

    print(f"{len(dates)} paired dates; warning threshold {th} (frozen from the fires-only series)")
    for name in ("fires_only", "with_sam"):
        s = result[name][str(th)]
        print(f"  {name:<11} POD {s['pod']}  FAR {s['far']}  CSI {s['csi']}")
    b = result["bootstrap"]
    print(f"  SAM - fires-only CSI {b['mean']:+.3f}  95% CI {b['ci95']}  P(SAM better) {b['p_sam_better']}")
    print("With PM2.5 at the start (threshold 0.9):")
    for name in ("fires_only", "with_sam"):
        s = result["with_persistence"][name]
        print(f"  {name:<11} POD {s['pod']}  FAR {s['far']}  CSI {s['csi']}")
    b = result["bootstrap_with_persistence"]
    print(f"  SAM - fires-only CSI {b['mean']:+.3f}  95% CI {b['ci95']}  P(SAM better) {b['p_sam_better']}")
    print("Per date:")
    for d in per_day:
        print(f"  {d['date']}  {d['smoky']:>2}/{d['cities']} smoky  plumes {d['plumes']} ({d['plume_area_km2']} km2)  "
              f"CSI {d['fires_only_csi']} -> {d['with_sam_csi']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
