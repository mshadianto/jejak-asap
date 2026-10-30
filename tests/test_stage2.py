import datetime as dt

import numpy as np

from jejak_asap import transport
from jejak_asap.geo import BBox, GeoImage
from jejak_asap.impact import FireSource, RiskRules, SourcePlume, haversine_km, score_cities, simulate, render_frame
from jejak_asap.transport import Ensemble, Particles, WindGrid, perturb, points_in_polygon, speed_dir_to_uv, step

T0 = dt.datetime(2019, 9, 14, 5)


def uniform_grid(u_kmh: float, v_kmh: float, hours: int = 24) -> WindGrid:
    lons, lats = np.arange(110.0, 118.1, 1.0), np.arange(-6.0, 0.1, 1.0)
    shape = (hours + 1, len(lats), len(lons))
    return WindGrid(lons, lats, [T0 + dt.timedelta(hours=h) for h in range(hours + 1)],
                    np.full(shape, u_kmh), np.full(shape, v_kmh))


def test_wind_from_south_blows_north():
    u, v = speed_dir_to_uv(np.array([10.0]), np.array([180.0]))
    assert abs(u[0]) < 1e-9 and abs(v[0] - 10) < 1e-9


def test_perturb_rotates_clockwise():
    u, v = perturb(np.array([10.0]), np.array([0.0]), np.array([90.0]), np.array([1.0]))  # east -> south
    assert abs(u[0]) < 1e-9 and abs(v[0] + 10) < 1e-9


def test_particle_moves_east_100km_in_10h():
    grid = uniform_grid(10.0, 0.0)
    p = Particles(np.array([113.0]), np.array([-3.0]), np.array([1.0]), np.array([0]), np.array([0]), np.array([1]))
    ens = Ensemble(np.array([0.0]), np.array([1.0]))
    rng = np.random.default_rng(0)
    for h in range(10):
        step(grid, p, float(h), 1.0, ens, 0.0, rng)
    assert abs(haversine_km(113.0, -3.0, p.lon[0], p.lat[0]) - 100.0) < 1.0
    assert abs(p.lat[0] + 3.0) < 1e-6


def test_points_in_polygon_square():
    poly = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], float)
    pts = np.array([[0.5, 0.5], [1.5, 0.5], [-0.1, 0.2]])
    assert points_in_polygon(pts, poly).tolist() == [True, False, False]


def test_downwind_city_high_upwind_city_safe():
    grid = uniform_grid(0.0, 12.0)  # wind from south: smoke goes north ~290 km in 24 h
    ring = [(113.85, -3.05), (113.95, -3.05), (113.95, -2.95), (113.85, -2.95)]
    plumes = [SourcePlume(1, [ring], 100.0)]
    fires = [FireSource(1, 113.9, -3.05, 50.0)]
    cities = {"Utara": (113.9, -2.0), "Selatan": (113.9, -4.0)}
    sim = simulate(grid, plumes, fires, hours=24, members=10, initial_per_member=50)
    impacts = {c.city: c for c in score_cities(sim, cities, BBox(110, -6, 118, 0), fires, RiskRules())}
    north, south = impacts["Utara"], impacts["Selatan"]
    assert north.risk in ("TINGGI", "SEDANG") and north.max_probability >= 0.5
    # ~105 km at 12 km/h -> roughly 7-9 hours after the start
    arrival = dt.datetime.fromisoformat(north.likely_arrival_utc)
    assert 5 <= (arrival - T0).total_seconds() / 3600 <= 10
    assert south.risk == "AMAN" and south.likely_arrival_utc is None


def test_fetch_wind_grid_parses_multi_location(monkeypatch):
    def fake_get(url, params, retries):
        lats = params["latitude"].split(",")
        times = [(T0 + dt.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(-5, 30)]
        return [{"hourly": {"time": times, "wind_speed_100m": [20.0] * len(times),
                            "wind_direction_100m": [90.0] * len(times)}} for _ in lats]
    monkeypatch.setattr(transport, "_get_json", fake_get)
    g = transport.fetch_wind_grid(BBox(113.2, -3.4, 114.6, -2.2), T0, 24)
    assert g.u.shape == (25, len(g.lats), len(g.lons))
    assert g.times[0] == T0 and np.allclose(g.u, -20.0) and np.allclose(g.v, 0.0, atol=1e-9)


def test_render_frame_draws(tmp_path):
    from PIL import Image
    geo = GeoImage(BBox(110, -6, 118, 0), 400, 300)
    p = Particles(np.array([113.0]), np.array([-3.0]), np.array([1.0]), np.array([0]), np.array([0]), np.array([1]))
    img = render_frame(Image.new("RGB", (400, 300)), geo, p, [], "uji")
    assert img.size == (400, 300)
