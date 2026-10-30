import datetime as dt
import json

from jejak_asap.backtest import judge, run_backtest, scores

T0 = dt.datetime(2023, 10, 13, 5)


def series(values):
    return [(T0 + dt.timedelta(hours=h), v) for h, v in enumerate(values)]


def city(name, prob, arrival_h=None, risk="TINGGI"):
    return {"city": name, "lon": 113.9, "lat": -2.2, "risk": risk, "max_probability": prob,
            "likely_arrival_utc": None if arrival_h is None else (T0 + dt.timedelta(hours=arrival_h)).isoformat(),
            "probability_by_hour": [prob] * 25}


def test_outcomes_and_timing():
    hit = judge(city("A", 0.9, arrival_h=5), series([20] * 3 + [80] * 22))
    assert hit.outcome == "hit" and hit.arrival_error_h == 2.0 and not hit.already_affected_at_start
    assert judge(city("B", 0.1), series([20, 90])).outcome == "miss"
    assert judge(city("C", 0.8, 3), series([10, 20, 30])).outcome == "false_alarm"
    assert judge(city("D", 0.0), series([10, 20])).outcome == "correct_negative"
    assert judge(city("E", 0.7, 1), series([None, None])).outcome == "no_data"
    already = judge(city("F", 0.9, 0), series([120, 130]))
    assert already.outcome == "hit" and already.already_affected_at_start and already.arrival_error_h is None


def test_scores():
    vs = [judge(city("A", 0.9, 2), series([10, 10, 80])), judge(city("B", 0.1), series([90])),
          judge(city("C", 0.8, 1), series([10])), judge(city("D", 0.0), series([10]))]
    s = scores(vs)
    assert (s["hit"], s["miss"], s["false_alarm"], s["correct_negative"]) == (1, 1, 1, 1)
    assert s["pod"] == 0.5 and s["far"] == 0.5 and s["csi"] == 0.33 and s["accuracy"] == 0.5


def test_run_backtest_with_given_observations(tmp_path):
    impact = {"analysis_time_utc": T0.isoformat(), "hours": 3,
              "cities": [city("Palangka Raya", 0.95, 1), city("Banjarmasin", 0.0, risk="AMAN")]}
    p = tmp_path / "impact.json"
    p.write_text(json.dumps(impact))
    obs = {"Palangka Raya": series([30, 70, 200, 250]), "Banjarmasin": series([15, 18, 20, 22])}
    r = run_backtest(p, obs)
    assert r["scores"]["csi"] == 1.0 and r["scores"]["accuracy"] == 1.0
    assert r["reference"] == "user observations CSV"
    assert len(r["threshold_sweep"]) == 9


def test_distance_baselines():
    from jejak_asap.backtest import distance_baselines
    cities = [{**city("Dekat", 0.0), "distance_to_nearest_fire_km": 10.0},
              {**city("Jauh", 0.0), "distance_to_nearest_fire_km": 300.0}]
    obs = {"Dekat": series([80]), "Jauh": series([10])}
    rows = {r["radius_km"]: r for r in distance_baselines(cities, obs)}
    assert rows[25]["csi"] == 1.0 and rows[150]["csi"] == 1.0


def test_series_evaluate_pools_city_days():
    import run_series
    t = [(T0 + dt.timedelta(hours=h)).isoformat() for h in range(3)]
    day = {"cities": [{**city("Kena", 0.9, 1), "distance_to_nearest_fire_km": 200.0},
                      {**city("Bersih", 0.0, risk="AMAN"), "distance_to_nearest_fire_km": 10.0}],
           "observations": {"Kena": [[t[0], 20], [t[1], 90], [t[2], 120]], "Bersih": [[x, 10] for x in t]}}
    s = run_series.evaluate([day, day], 55.5)
    assert s["model"]["0.5"]["csi"] == 1.0 and s["smoky_city_days"] == 2
    assert s["baselines"]["50"]["csi"] == 0.0   # the near city is clean, the far one smoky
    assert s["best_threshold"] <= 0.9


def test_simulate_fires_only_releases_particles():
    import numpy as np
    from jejak_asap.impact import FireSource, simulate
    from tests.test_stage2 import uniform_grid
    sim = simulate(uniform_grid(0.0, 10.0), [], [FireSource(1, 113.9, -3.0, 20.0)], hours=6,
                   members=4, initial_per_member=0)
    assert len(sim.snapshots[0].lon) == 0 and len(sim.snapshots[-1].lon) == 6 * 4 * 6
    assert np.all(sim.snapshots[-1].lat > -3.1)


def test_series_persistence_and_bootstrap():
    import run_series
    t = [(T0 + dt.timedelta(hours=h)).isoformat() for h in range(3)]
    day = {"cities": [{**city("Sudah", 0.0, risk="AMAN"), "distance_to_nearest_fire_km": 300.0},
                      {**city("Baru", 0.9, 1), "distance_to_nearest_fire_km": 300.0}],
           "observations": {"Sudah": [[x, 90] for x in t], "Baru": [[t[0], 10], [t[1], 80], [t[2], 90]]}}
    s = run_series.evaluate([day, day], 55.5, n_boot=50)
    assert s["combined"]["persistence"]["csi"] == 0.5
    assert s["combined"]["persistence+model 0.5"]["csi"] == 1.0
    assert s["new_arrivals"]["city_days"] == 2 and s["new_arrivals"]["became_smoky"] == 2
    assert s["bootstrap"][2]["mean"] == 0.5


def test_plumes_enter_after_spinup():
    from jejak_asap.impact import SourcePlume, simulate
    from tests.test_stage2 import uniform_grid
    ring = [(113.0, -3.0), (113.2, -3.0), (113.2, -2.8), (113.0, -2.8), (113.0, -3.0)]
    sim = simulate(uniform_grid(0.0, 10.0), [SourcePlume(1, [ring], 400.0)], [], hours=6, members=2,
                   initial_per_member=50, plume_at_h=3)
    assert len(sim.snapshots[3].lon) == 0 and len(sim.snapshots[4].lon) >= 100
    assert set(sim.snapshots[-1].release_h.tolist()) == {3}
