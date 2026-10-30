import math

import numpy as np
import pytest

from jejak_asap.firms import Hotspot, cluster_hotspots, filter_hotspots, parse_firms_csv
from jejak_asap.geo import BBox, GeoImage, destination, image_size_for, polygon_area_km2
from jejak_asap.muse import is_smoke, parse_verdict
from jejak_asap.pipeline import make_seeds, mask_to_polygons
from jejak_asap.sam import ObjectSeed, build_prompt, parse_sam_output
from jejak_asap.wind import Wind

BBOX = BBox(113.3, -3.6, 114.6, -2.5)

# Sample SAM 3.1 output from Meta's meta-sam-parser README (320x334 media).
SAM_SAMPLE = (
    "<0f>0<|box;x1=211;y1=228;x2=270;y2=254;w=320;h=334|>"
    "<|mask;x=0;y=0;data=27,60,~!!!!M!0c[0o91w?q1!pIH4pPRVp2B3'7`e.ioeAf6-k/#Xd8%dX9x(|>"
    ",1<|box;x1=155;y1=228;x2=202;y2=254;w=320;h=334|>"
    "<|mask;x=0;y=0;data=27,48,~!!!!J!0c[q=Pj_zs=*4C4(/./x#:/`S_GnD`=o3?X{emCgO$y@|>\n"
)


def hs(lon, lat, frp=10.0, conf="n", dn="D", t="0548"):
    return Hotspot(lon, lat, frp, conf, "2019-09-14", t, dn)


def test_pixel_roundtrip():
    w, h = image_size_for(BBOX, 1300)
    geo = GeoImage(BBOX, w, h)
    assert (w, h) == (1300, 1100)
    x, y = geo.to_pixel(113.95, -3.05)
    lon, lat = geo.to_lonlat(x + 0.5, y + 0.5)
    assert abs(lon - 113.95) < 0.002 and abs(lat + 3.05) < 0.002
    assert geo.to_pixel(BBOX.west, BBOX.north) == (0, 0)
    assert geo.to_pixel(BBOX.east, BBOX.south) == (w - 1, h - 1)


def test_destination_north_10km():
    lon, lat = destination(114.0, -3.0, 0.0, 10.0)
    assert abs(lon - 114.0) < 1e-6
    assert abs((lat + 3.0) * 111.19 - 10.0) < 0.05


def test_wind_bearing_is_opposite_of_from():
    assert Wind(10, 135).bearing_to_deg == 315  # SE wind pushes smoke NW


def test_area_of_one_degree_box_at_equator():
    ring = [(0, 0), (1, 0), (1, 1), (0, 1)]
    assert abs(polygon_area_km2(ring) - 111.19 ** 2) / 111.19 ** 2 < 0.01


def test_firms_csv_parse_and_filter():
    csv_text = (
        "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight\n"
        "-3.1,113.9,340,0.4,0.4,2019-09-14,548,N,VIIRS,n,1,300,12.5,D\n"
        "-3.1,113.9,330,0.4,0.4,2019-09-14,1806,N,VIIRS,n,1,290,5.0,N\n"
        "-3.2,113.8,320,0.4,0.4,2019-09-14,548,N,VIIRS,l,1,290,1.0,D\n"
        "-9.0,113.8,320,0.4,0.4,2019-09-14,548,N,VIIRS,h,1,290,1.0,D\n"
    )
    spots = parse_firms_csv(csv_text)
    assert len(spots) == 4 and spots[0].acq_time == "0548"
    kept = filter_hotspots(spots, BBOX)
    assert len(kept) == 1 and kept[0].frp == 12.5


def test_clusters_merge_neighbours_and_rank_by_frp():
    spots = [hs(113.90, -3.10, 5), hs(113.92, -3.11, 5), hs(114.40, -2.70, 50)]
    clusters = cluster_hotspots(spots, cell_deg=0.05)
    assert [len(c.hotspots) for c in clusters] == [1, 2]
    assert clusters[0].frp_sum == 50
    assert len(cluster_hotspots([hs(113.3 + (i % 6) * 0.2, -3.5 + (i // 6) * 0.3) for i in range(18)], max_clusters=16)) == 16


def test_seed_moves_downwind_and_stays_in_bbox():
    geo = GeoImage(BBOX, 1300, 1100)
    clusters = cluster_hotspots([hs(113.9, -3.3, 20)])
    seeds, infos = make_seeds(clusters, geo, Wind(15, 180), offset_km=6)  # from south -> drifts north
    assert infos[0].seed_lonlats[0][1] > infos[0].anchor_lonlats[0][1]
    assert seeds[0].positive[0][1] < geo.to_pixel(*infos[0].anchor_lonlats[0])[1]


def test_big_cluster_gets_several_points():
    geo = GeoImage(BBOX, 1300, 1100)
    line = [hs(113.80 + i * 0.01, -3.20, 5 + i) for i in range(12)]  # ~13 km fire line
    seeds, infos = make_seeds(cluster_hotspots(line), geo, None, offset_km=0)
    assert len(seeds) == 1 and len(seeds[0].positive) == 3


def test_min_hotspots_drops_singletons():
    spots = [hs(113.5, -3.0), hs(114.0, -3.3, 5), hs(114.01, -3.3, 5)]
    assert len(cluster_hotspots(spots, min_hotspots=2)) == 1


def test_prompt_grammar_matches_docs():
    text = build_prompt([ObjectSeed(1, [(800, 420)], []), ObjectSeed(2, [(560, 400)], [(100, 100)])], (1483, 812))
    assert text == (
        "<0f>1<|point;x=800;y=420;w=1483;h=812|>,"
        "2<|point;x=560;y=400;w=1483;h=812|>-<|point;x=100;y=100;w=1483;h=812|>"
    )
    with pytest.raises(ValueError):
        build_prompt([ObjectSeed(i, [(1, 1)], []) for i in range(17)], (10, 10))


def test_parse_sam_sample_places_masks_inside_boxes():
    objs = {o.object_id: o for o in parse_sam_output(SAM_SAMPLE, 320, 334)}
    assert set(objs) == {0, 1}
    m0 = objs[0].mask
    assert m0.shape == (334, 320) and m0.any()
    ys, xs = np.nonzero(m0)
    assert xs.min() >= 211 and xs.max() <= 270 and ys.min() >= 228 and ys.max() <= 254


def test_mask_to_polygons_square():
    geo = GeoImage(BBOX, 100, 100)
    mask = np.zeros((100, 100), bool)
    mask[20:60, 30:70] = True
    polys = mask_to_polygons(mask, geo)
    assert len(polys) == 1
    expected = 40 * 40 * math.prod(geo.km_per_pixel())
    assert abs(polygon_area_km2(polys[0]) - expected) / expected < 0.1


def test_muse_verdict_parsing():
    v = parse_verdict('```json\n{"label": "Mixed", "smoke_fraction": 0.7, "confidence": 0.8, "reason": "x"}\n```')
    assert v.label == "mixed" and is_smoke(v)
    assert parse_verdict("no json here").label == "other"
    assert not is_smoke(parse_verdict('{"label": "cloud"}'))


class _Resp:
    def __init__(self, text):
        self.output_text = text


class FakeClient:
    """Stands in for the Meta Model API: SAM returns the docs sample, Muse says smoke."""

    def __init__(self):
        self.calls = {"sam": 0, "muse": 0}
        self.responses = self

    def create(self, model, input, **kwargs):
        if model.startswith("sam"):
            self.calls["sam"] += 1
            return _Resp(SAM_SAMPLE)
        self.calls["muse"] += 1
        return _Resp('{"label": "smoke", "smoke_fraction": 0.9, "confidence": 0.8, "reason": "grey plume"}')


def test_full_run_with_fake_api_resumes_from_cache(tmp_path):
    from PIL import Image
    from jejak_asap.pipeline import run_stage1
    bbox = BBox(113.0, -3.0, 113.32, -2.666)
    geo = GeoImage(bbox, 320, 334)
    image = Image.new("RGB", (320, 334), (90, 110, 90))
    spots = [hs(113.2, -2.75, 10), hs(113.21, -2.75, 10)]
    client = FakeClient()
    rep = run_stage1(image=image, geo=geo, hotspots=spots, date="2019-09-14", out_dir=tmp_path,
                     client=client, log=lambda *_: None)
    assert client.calls == {"sam": 1, "muse": 2}
    assert rep["accepted_plumes"] == 2 and rep["accepted_area_km2"] > 0
    for name in ("plumes.geojson", "overlay.png", "report.json", "objects_00.npz", "sam_raw_00.txt", "muse_0.json", "muse_1.json"):
        assert (tmp_path / name).exists(), name
    # Second run reuses SAM output, decoded masks and Muse verdicts: no new API calls.
    rep2 = run_stage1(image=image, geo=geo, hotspots=spots, date="2019-09-14", out_dir=tmp_path,
                      client=client, reuse_sam=True, log=lambda *_: None)
    assert client.calls == {"sam": 1, "muse": 2}
    assert rep2["accepted_area_km2"] == rep["accepted_area_km2"]


def test_time_budget_stops_cleanly(tmp_path):
    import time
    from PIL import Image
    from jejak_asap.pipeline import IncompleteRun, run_stage1
    geo = GeoImage(BBox(113.0, -3.0, 113.32, -2.666), 320, 334)
    with pytest.raises(IncompleteRun):
        run_stage1(image=Image.new("RGB", (320, 334)), geo=geo, hotspots=[hs(113.2, -2.75), hs(113.21, -2.75)],
                   date="2019-09-14", out_dir=tmp_path, client=FakeClient(),
                   deadline=time.monotonic() - 1, log=lambda *_: None)
    assert (tmp_path / "sam_raw_00.txt").exists()  # the finished stage was kept
