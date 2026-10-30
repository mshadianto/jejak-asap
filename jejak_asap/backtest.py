"""Backtest: compare stage 2 city forecasts with hourly PM2.5.

Reference data: CAMS global PM2.5 via the Open-Meteo Air Quality API (hourly; empty for
2019 when checked, filled for 2023). CAMS is itself a model, not a sensor; Grosvenor et al.
(2024, Communications Earth & Environment, doi:10.1038/s43247-024-01813-w) found CAMS
tracks low-cost sensor data in Palangka Raya during the 2019 fires (about 5% lower on
days below 100 ug/m3, about 35% higher on days above it).
Local sensor CSVs can be used instead with `load_observations_csv`.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
UNHEALTHY_PM25 = 55.5  # ug/m3; where the US EPA AQI category "Unhealthy" begins


@dataclass
class CityVerdict:
    city: str
    predicted_risk: str
    predicted_probability: float
    predicted_affected: bool
    predicted_arrival_utc: str | None
    observed_max_pm25: float | None
    observed_mean_pm25: float | None
    observed_affected: bool | None
    observed_arrival_utc: str | None     # first hour at or above the threshold
    already_affected_at_start: bool | None
    outcome: str                         # hit, miss, false_alarm, correct_negative, no_data
    arrival_error_h: float | None        # predicted minus observed, hits that started clean only


def fetch_pm25(cities: dict[str, tuple[float, float]], start: dt.datetime, hours: int,
               retries: int = 3) -> dict[str, list[tuple[dt.datetime, float | None]]]:
    names = list(cities)
    end = start + dt.timedelta(hours=hours)
    params = {
        "latitude": ",".join(f"{cities[n][1]:.3f}" for n in names),
        "longitude": ",".join(f"{cities[n][0]:.3f}" for n in names),
        "hourly": "pm2_5",
        "start_date": start.date().isoformat(),
        "end_date": end.date().isoformat(),
        "timezone": "UTC",
    }
    for attempt in range(retries):
        try:
            resp = requests.get(AIR_QUALITY_URL, params=params, timeout=60)
            if resp.status_code == 429 and attempt < retries - 1:
                time.sleep(10 * (attempt + 1))
                continue
            resp.raise_for_status()
            break
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))
    data = resp.json()
    items = data if isinstance(data, list) else [data]
    out = {}
    for name, item in zip(names, items):
        series = []
        for t, v in zip(item["hourly"]["time"], item["hourly"]["pm2_5"]):
            ts = dt.datetime.fromisoformat(t)
            if start <= ts <= end:
                series.append((ts, None if v is None else float(v)))
        out[name] = series
    return out


def load_observations_csv(path: Path) -> dict[str, list[tuple[dt.datetime, float | None]]]:
    """CSV with columns city,time_utc,pm25 (ISO times). For sensor data such as PurpleAir or ISPU."""
    out: dict[str, list] = {}
    with Path(path).open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            v = row.get("pm25", "")
            out.setdefault(row["city"], []).append(
                (dt.datetime.fromisoformat(row["time_utc"]), float(v) if v not in ("", "NA", "nan") else None))
    for series in out.values():
        series.sort()
    return out


def judge(city: dict, series: list[tuple[dt.datetime, float | None]], prob_threshold: float = 0.5,
          pm_threshold: float = UNHEALTHY_PM25) -> CityVerdict:
    valid = [(t, v) for t, v in series if v is not None]
    predicted = city["max_probability"] >= prob_threshold
    if not valid:
        return CityVerdict(city["city"], city["risk"], city["max_probability"], predicted,
                           city.get("likely_arrival_utc"), None, None, None, None, None, "no_data", None)
    values = [v for _, v in valid]
    observed = max(values) >= pm_threshold
    obs_arrival = next((t for t, v in valid if v >= pm_threshold), None)
    already = valid[0][1] >= pm_threshold
    if predicted and observed:
        outcome = "hit"
    elif observed:
        outcome = "miss"
    elif predicted:
        outcome = "false_alarm"
    else:
        outcome = "correct_negative"
    err = None
    if outcome == "hit" and not already and city.get("likely_arrival_utc") and obs_arrival:
        pred = dt.datetime.fromisoformat(city["likely_arrival_utc"])
        err = (pred - obs_arrival).total_seconds() / 3600
    return CityVerdict(city["city"], city["risk"], city["max_probability"], predicted,
                       city.get("likely_arrival_utc"), round(max(values), 1), round(sum(values) / len(values), 1),
                       observed, None if obs_arrival is None else obs_arrival.isoformat(), already, outcome,
                       None if err is None else round(err, 1))


def scores(verdicts: list[CityVerdict]) -> dict:
    n = {k: sum(v.outcome == k for v in verdicts) for k in ("hit", "miss", "false_alarm", "correct_negative", "no_data")}
    a, b, c, d = n["hit"], n["false_alarm"], n["miss"], n["correct_negative"]
    errors = [v.arrival_error_h for v in verdicts if v.arrival_error_h is not None]
    return {
        **n,
        "pod": None if a + c == 0 else round(a / (a + c), 2),            # share of real smoke events caught
        "far": None if a + b == 0 else round(b / (a + b), 2),            # share of warnings that were wrong
        "csi": None if a + b + c == 0 else round(a / (a + b + c), 2),    # overall skill, 1 = perfect
        "accuracy": None if a + b + c + d == 0 else round((a + d) / (a + b + c + d), 2),
        "timing_cases": len(errors),
        "mean_abs_arrival_error_h": None if not errors else round(sum(abs(e) for e in errors) / len(errors), 1),
        "mean_arrival_bias_h": None if not errors else round(sum(errors) / len(errors), 1),
    }


def threshold_sweep(cities: list[dict], observations: dict, pm_threshold: float = UNHEALTHY_PM25) -> list[dict]:
    """CSI for a range of probability thresholds: the data-driven starting point for calibration."""
    rows = []
    for th in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        vs = [judge(c, observations.get(c["city"], []), th, pm_threshold) for c in cities]
        s = scores(vs)
        rows.append({"prob_threshold": th, "pod": s["pod"], "far": s["far"], "csi": s["csi"]})
    return rows


def distance_baselines(cities: list[dict], observations: dict, pm_threshold: float = UNHEALTHY_PM25,
                       radii_km: tuple[float, ...] = (25, 50, 75, 100, 150)) -> list[dict]:
    """Naive rule to beat: "every city within R km of a fire gets smoke", no wind at all."""
    rows = []
    for r in radii_km:
        fake = [{**c, "max_probability": 1.0 if c.get("distance_to_nearest_fire_km", 1e9) <= r else 0.0,
                 "likely_arrival_utc": None} for c in cities]
        s = scores([judge(c, observations.get(c["city"], []), 0.5, pm_threshold) for c in fake])
        rows.append({"radius_km": r, "pod": s["pod"], "far": s["far"], "csi": s["csi"]})
    return rows


def run_backtest(impact_path: Path, observations: dict | None = None, prob_threshold: float = 0.5,
                 pm_threshold: float = UNHEALTHY_PM25) -> dict:
    impact = json.loads(Path(impact_path).read_text(encoding="utf-8"))
    start = dt.datetime.fromisoformat(impact["analysis_time_utc"])
    hours = int(impact["hours"])
    cities = impact["cities"]
    reference = "user observations CSV"
    if observations is None:
        observations = fetch_pm25({c["city"]: (c["lon"], c["lat"]) for c in cities}, start, hours)
        reference = "CAMS PM2.5 via Open-Meteo Air Quality API (model reference, not sensors)"
    verdicts = [judge(c, observations.get(c["city"], []), prob_threshold, pm_threshold) for c in cities]
    return {
        "analysis_time_utc": start.isoformat(),
        "hours": hours,
        "reference": reference,
        "pm_threshold_ug_m3": pm_threshold,
        "prob_threshold": prob_threshold,
        "scores": scores(verdicts),
        "threshold_sweep": threshold_sweep(cities, observations, pm_threshold),
        "distance_baselines": distance_baselines(cities, observations, pm_threshold),
        "cities": [asdict(v) for v in verdicts],
        "series": {name: [[t.isoformat(), v] for t, v in s] for name, s in observations.items()},
    }
