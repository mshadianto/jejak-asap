"""Write audience alerts (warga, sekolah, puskesmas) for the cities at risk in one forecast.

    python run_alerts.py --forecast out/series_sam_local/2023-10-15.json --city "Palangka Raya"
    python run_alerts.py --forecast out/.../stage2_archive_100m/impact.json          # all cities at risk
    python run_alerts.py --forecast ... --no-muse                                    # templates only

Rules choose the actions; Muse Spark only phrases them, and every Muse message is checked
(numbers, forbidden claims, length) before it is used. Output: alerts.json and alerts.md
next to the forecast file (or in --out).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from jejak_asap.alerts import facts_for_city, write_alerts


def latest_pm25(forecast: dict, city: str) -> float | None:
    """PM2.5 at the forecast hour, when the file carries observations (series days do)."""
    series = forecast.get("observations", {}).get(city) or []
    for t, v in series:
        if v is not None and t[:13] == forecast["analysis_time_utc"][:13]:
            return float(v)
    return None


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
    ap = argparse.ArgumentParser(description="Audience alerts from a Jejak Asap forecast")
    ap.add_argument("--forecast", type=Path, required=True, help="impact.json or a run_series day file")
    ap.add_argument("--city", action="append", help="only these cities (repeatable); default: every city not AMAN")
    ap.add_argument("--no-muse", action="store_true", help="use the plain templates, no API call")
    ap.add_argument("--out", type=Path, help="output folder (default: next to the forecast)")
    args = ap.parse_args(argv)

    forecast = json.loads(args.forecast.read_text(encoding="utf-8"))
    def wanted(c):
        if args.city:
            return c["city"] in args.city
        now = latest_pm25(forecast, c["city"])
        return c["risk"] != "AMAN" or (now is not None and now >= 55.5)
    cities = [c for c in forecast["cities"] if wanted(c)]
    client, model = None, None
    if not args.no_muse:
        from openai import OpenAI
        from jejak_asap.config import load_settings
        s = load_settings()
        if s.meta_api_key:
            client = OpenAI(base_url=s.meta_base_url, api_key=s.meta_api_key, timeout=90, max_retries=1)
            model = s.muse_model
        else:
            print("MODEL_API_KEY not set: using templates")

    results = []
    for c in cities:
        facts = facts_for_city(c, forecast["analysis_time_utc"], latest_pm25(forecast, c["city"]))
        alerts = write_alerts(facts, client, model or "muse-spark-1.3")
        results.append({"facts": asdict(facts), "alerts": [asdict(a) for a in alerts]})
        print(f"\n== {facts.city}: {facts.risk} (prakiraan {facts.forecast_risk}), peluang {facts.probability_pct}%, "
              f"tiba {facts.arrival_wib or '-'}, PM2.5 sekarang {facts.pm25_now if facts.pm25_now is not None else '-'}")
        for a in alerts:
            note = "" if a.source == "muse" else f"  [template{': ' + '; '.join(a.problems) if a.problems else ''}]"
            print(f"-- {a.audience}{note}\n{a.text}")

    out = args.out or args.forecast.parent
    out.mkdir(parents=True, exist_ok=True)
    stem = args.forecast.stem
    (out / f"alerts_{stem}.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    md = []
    for r in results:
        f = r["facts"]
        md.append(f"## {f['city']} — {f['risk']}, peluang {f['probability_pct']}%, tiba {f['arrival_wib'] or '-'}\n")
        for a in r["alerts"]:
            md.append(f"**{a['audience']}** ({a['source']})\n\n{a['text']}\n")
    (out / f"alerts_{stem}.md").write_text("\n".join(md), encoding="utf-8")
    print(f"\nwrote {out / f'alerts_{stem}.json'} and .md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
