import json
from types import SimpleNamespace

from jejak_asap.alerts import (AUDIENCES, check_message, facts_for_city, pm25_category, template_message,
                               write_alerts)

CITY = {"city": "Palangka Raya", "risk": "TINGGI", "max_probability": 0.85, "likely_arrival_utc": "2023-10-15T06:00:00",
        "hours_affected": 12}


class FakeClient:
    def __init__(self, text):
        self.text = text
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        return SimpleNamespace(output_text=self.text)


def facts():
    return facts_for_city(CITY, "2023-10-15T06:00:00", pm25_now=137.4)


def test_categories_follow_bmkg():
    assert pm25_category(10) == "Baik" and pm25_category(55.4) == "Sedang" and pm25_category(55.5) == "Tidak Sehat"
    assert pm25_category(200) == "Sangat Tidak Sehat" and pm25_category(300) == "Berbahaya" and pm25_category(None) is None


def test_facts_and_templates():
    f = facts()
    assert f.probability_pct == 85 and f.pm25_now == 137 and f.already_unhealthy and f.arrival_wib is None
    clean = facts_for_city(CITY, "2023-10-15T06:00:00", pm25_now=30)
    assert clean.arrival_wib == "15 Okt 13.00 WIB" and not clean.already_unhealthy
    for a in AUDIENCES:
        text = template_message(f, a)
        assert check_message(text, f, a) == [], (a, check_message(text, f, a))


def test_checks_catch_invented_numbers_and_claims():
    f = facts()
    bad = "Asap di Palangka Raya peluang 85%, tiba 15.00 WIB. Sekolah diliburkan, pasti aman di rumah."
    problems = check_message(bad, f, "sekolah")
    assert any("15.00" in p for p in problems)
    assert any("liburkan" in p for p in problems) and any("pasti" in p for p in problems)


def test_muse_messages_used_only_when_clean():
    good = {a: template_message(facts(), a) for a in AUDIENCES}
    good["warga"] = "Palangka Raya: peluang asap 85%, mulai 15 Okt 13.00 WIB, PM2.5 saat ini 137 µg/m³ (Tidak Sehat). Bayi, balita, lansia dan ibu hamil tetap di rumah, tutup jendela, pakai masker bila keluar, ke puskesmas bila batuk."
    good["puskesmas"] = "Kirim obat X ke Palangka Raya, peluang 85%, 300 pasien."
    alerts = {a.audience: a for a in write_alerts(facts(), FakeClient(json.dumps(good)))}
    assert alerts["warga"].source == "muse" and alerts["sekolah"].source == "muse"
    assert alerts["puskesmas"].source == "template" and any("300" in p for p in alerts["puskesmas"].problems)
    assert all("bukan informasi resmi" in a.text for a in alerts.values())


def test_no_alert_when_aman_and_template_on_api_error():
    assert write_alerts(facts_for_city({**CITY, "risk": "AMAN", "max_probability": 0.0}, "2023-10-15T06:00:00")) == []

    class Broken:
        responses = SimpleNamespace(create=lambda **kw: (_ for _ in ()).throw(RuntimeError("timeout")))
    alerts = write_alerts(facts(), Broken())
    assert all(a.source == "template" and "timeout" in a.problems[0] for a in alerts)


def test_pm25_must_be_quoted_with_unit_and_no_arrival_wording():
    f = facts()
    assert "current PM2.5 with unit missing" in check_message("Palangka Raya peluang 85%. Udara 137 kategori Tidak Sehat.", f, "sekolah")
    g = facts_for_city({**CITY, "likely_arrival_utc": None, "risk": "SEDANG"}, "2023-10-15T06:00:00")
    assert all("jam perkiraan" not in x for xs in g.actions.values() for x in xs)


def test_already_unhealthy_means_act_now():
    f = facts()  # PM2.5 137 at issue time
    assert any("mulai sekarang" in x for x in f.actions["warga"])
    assert not any("jam perkiraan" in x for xs in f.actions.values() for x in xs)


def test_measured_unhealthy_air_overrides_a_milder_forecast():
    f = facts_for_city({**CITY, "risk": "SEDANG"}, "2023-10-15T06:00:00", pm25_now=190)
    assert f.risk == "TINGGI" and f.forecast_risk == "SEDANG" and "posyandu" in " ".join(f.actions["puskesmas"])


def test_probability_needs_a_percent_sign():
    f = facts_for_city(CITY, "2023-10-15T06:00:00", pm25_now=30)
    assert "probability with % missing" in check_message("Palangka Raya peluang 85. PM2.5 saat ini 30 µg/m³.", f, "sekolah")
    assert "probability with % missing" not in check_message("Palangka Raya peluang 85 persen.", f, "sekolah")
