"""Audience alerts: rules decide what to do, Muse only writes it in plain Indonesian.

Safety-relevant content (risk level, who is vulnerable, which actions) comes from fixed rules
grounded in public guidance. Muse Spark turns those facts into short messages for three
audiences. Every Muse message is checked: it may only use numbers and times we gave it, must
not contain forbidden claims, and must stay short. If a check fails, a plain template is used.

Sources for the rules:
- BMKG PM2.5 categories (https://www.bmkg.go.id/kualitas-udara/pm25): Baik 0-15,5;
  Sedang 15,6-55,4; Tidak Sehat 55,5-150,4; Sangat Tidak Sehat 150,5-250,4; Berbahaya >250,5 ug/m3.
- Kemenkes, 15 Oct 2026, "Menkes imbau kelompok rentan batasi aktivitas di luar rumah saat
  kabut asap": vulnerable = bayi, balita, lansia, ibu hamil; limit outdoor time; mask (N95 if
  available), glasses, long sleeves; go to the puskesmas if cough or eye/skin irritation persists.
- Kemenkes, 15 Oct 2015, "Penggunaan masker pada asap kebakaran hutan": N95 only with correct
  fit; not for children, pregnant women, older adults or people with heart or chronic lung disease.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

AUDIENCES = ("warga", "sekolah", "puskesmas")
DISCLAIMER = "Prakiraan model Jejak Asap, bukan informasi resmi BMKG/BPBD."

PM25_CATEGORIES = (  # upper bound inclusive, ug/m3, BMKG
    (15.5, "Baik"),
    (55.4, "Sedang"),
    (150.4, "Tidak Sehat"),
    (250.4, "Sangat Tidak Sehat"),
    (float("inf"), "Berbahaya"),
)

ACTIONS = {
    "warga": {
        "TINGGI": [
            "Bayi, balita, lansia dan ibu hamil sebaiknya tetap di dalam rumah sejak sebelum jam perkiraan asap tiba.",
            "Tutup jendela dan pintu; kurangi aktivitas di luar rumah.",
            "Jika harus keluar: pakai masker (N95 bila ada dan pas di wajah, untuk orang dewasa sehat), kacamata dan baju lengan panjang.",
            "Segera ke puskesmas bila batuk, mata perih atau iritasi kulit tidak hilang.",
        ],
        "SEDANG": [
            "Kelompok rentan (bayi, balita, lansia, ibu hamil) kurangi aktivitas di luar rumah pada jam perkiraan asap.",
            "Siapkan masker dan pantau kualitas udara.",
        ],
        "RENDAH": ["Pantau kabar kualitas udara; asap mungkin lewat sebentar."],
    },
    "sekolah": {
        "TINGGI": [
            "Pindahkan upacara, olahraga dan kegiatan luar ruang ke dalam kelas pada jam perkiraan asap.",
            "Tutup jendela kelas dan awasi siswa dengan asma atau gangguan napas.",
            "Koordinasikan keputusan jam belajar dengan dinas pendidikan setempat.",
        ],
        "SEDANG": [
            "Kurangi kegiatan luar ruang pada jam perkiraan asap.",
            "Awasi siswa dengan asma atau gangguan napas.",
        ],
        "RENDAH": ["Pantau kualitas udara sebelum kegiatan luar ruang."],
    },
    "puskesmas": {
        "TINGGI": [
            "Siapkan obat untuk ISPA dan iritasi mata serta kesiapan oksigen.",
            "Sampaikan imbauan ke posyandu dan kader untuk bayi, balita, lansia dan ibu hamil.",
            "Catat kunjungan ISPA harian untuk memantau dampak asap.",
        ],
        "SEDANG": [
            "Siapkan obat untuk ISPA dan iritasi mata.",
            "Sampaikan imbauan ke kelompok rentan melalui kader.",
        ],
        "RENDAH": ["Pantau kunjungan ISPA."],
    },
}

FORBIDDEN = (  # claims the model is not allowed to make
    r"\bpasti\b", r"dijamin", r"\baman sepenuhnya\b", r"sekolah (di)?liburkan", r"\bmeliburkan\b",
    r"evakuasi", r"\bdiagnos", r"resep", r"obat\s+\w+in\b",
)
MAX_CHARS = {"warga": 560, "sekolah": 700, "puskesmas": 700}


def pm25_category(value: float | None) -> str | None:
    if value is None:
        return None
    for upper, name in PM25_CATEGORIES:
        if value <= upper:
            return name
    return PM25_CATEGORIES[-1][1]


@dataclass
class AlertFacts:
    city: str
    risk: str                      # TINGGI / SEDANG / RENDAH / AMAN used for the actions
    forecast_risk: str             # what the forecast alone said
    probability_pct: int           # chance of smoke within 25 km in the next 24 h
    issued_wib: str                # e.g. "15 Okt 13.00 WIB"
    arrival_wib: str | None        # first hour at least half the wind scenarios agree
    hours_affected: int
    pm25_now: float | None = None  # latest measured PM2.5, ug/m3, if any
    pm25_category: str | None = None
    already_unhealthy: bool = False  # PM2.5 already >= 55.5 at issue time: act now, no arrival hour
    actions: dict = field(default_factory=dict)

    def allowed_numbers(self) -> set[str]:
        text = json.dumps(asdict(self), ensure_ascii=False)
        nums = set(re.findall(r"\d+(?:[.,]\d+)?", text))
        nums |= {n.replace(".", ",") for n in nums} | {n.replace(",", ".") for n in nums}
        nums |= {"24", "25", "2023", "2024", "2025", "2026", "95"}  # fixed context: 24 h window, 25 km, N95
        return nums


MONTHS_ID = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def wib_label(iso_utc: str | None) -> str | None:
    if not iso_utc:
        return None
    import datetime as dt
    t = dt.datetime.fromisoformat(iso_utc) + dt.timedelta(hours=7)
    return f"{t.day} {MONTHS_ID[t.month - 1]} {t:%H}.00 WIB"


def facts_for_city(city: dict, analysis_utc: str, pm25_now: float | None = None) -> AlertFacts:
    """`city` is one entry of impact.json / a series day's `cities` list."""
    already = pm25_now is not None and pm25_now >= 55.5   # BMKG "Tidak Sehat" or worse
    # Measured air beats the forecast: unhealthy air now gets the TINGGI actions whatever the model says.
    risk = "TINGGI" if already else city["risk"]
    return AlertFacts(
        city=city["city"], risk=risk, forecast_risk=city["risk"], probability_pct=round(100 * city["max_probability"]),
        issued_wib=wib_label(analysis_utc),
        arrival_wib=None if already else wib_label(city.get("likely_arrival_utc")),
        hours_affected=int(city.get("hours_affected", 0)),
        pm25_now=None if pm25_now is None else round(pm25_now),
        pm25_category=pm25_category(pm25_now), already_unhealthy=already,
        actions={a: [_when(x, city.get("likely_arrival_utc"), already) for x in ACTIONS[a].get(risk, [])]
                 for a in AUDIENCES},
    )


def _when(action: str, arrival_utc: str | None, already_smoky: bool = False) -> str:
    """Actions mention the forecast arrival hour. If the air is already unhealthy, act now;
    without an arrival hour, act when the smoke comes."""
    if already_smoky:
        return (action.replace("sejak sebelum jam perkiraan asap tiba", "mulai sekarang")
                      .replace("pada jam perkiraan asap", "mulai sekarang"))
    if arrival_utc:
        return action
    return (action.replace("sejak sebelum jam perkiraan asap tiba", "bila asap mulai terasa")
                  .replace("pada jam perkiraan asap", "bila asap mulai terasa"))


WRITE_PROMPT = """Kamu menulis peringatan kabut asap untuk tiga pembaca di kota {city}, Kalimantan.
Gunakan HANYA fakta di JSON ini. Jangan menambah angka, jam, tempat, obat, atau saran lain.
Sertakan semua tindakan dari daftar "actions" untuk tiap pembaca, boleh diringkas tapi jangan diubah maknanya.
Sebutkan peluang dan jam perkiraan asap tiba bila ada.
Bila already_unhealthy true: tulis bahwa udara sudah tidak sehat sekarang, jangan menulis jam tiba.
Bila pm25_now ada, tulis persis "PM2.5 saat ini <pm25_now> µg/m³ (<pm25_category>)" di setiap pesan. Bahasa Indonesia sederhana, sopan, tidak menakut-nakuti.

Pembaca:
- warga: pesan WhatsApp, maksimal 4 kalimat pendek.
- sekolah: memo singkat untuk kepala sekolah.
- puskesmas: catatan operasional singkat untuk kepala puskesmas.

Fakta:
{facts}

Jawab hanya JSON: {{"warga": "...", "sekolah": "...", "puskesmas": "..."}}"""


def template_message(facts: AlertFacts, audience: str) -> str:
    when = ("; udara sudah tidak sehat sekarang" if facts.already_unhealthy else
            f" mulai sekitar {facts.arrival_wib}" if facts.arrival_wib else " dalam 24 jam ke depan")
    head = {"warga": f"Waspada asap di {facts.city}", "sekolah": f"Memo asap untuk sekolah di {facts.city}",
            "puskesmas": f"Kesiapan puskesmas, {facts.city}"}[audience]
    now = f" PM2.5 saat ini {facts.pm25_now} µg/m³ ({facts.pm25_category})." if facts.pm25_now is not None else ""
    lines = [f"{head}: peluang asap {facts.probability_pct}%{when}.{now}"] + facts.actions[audience]
    return " ".join(lines)


def check_message(text: str, facts: AlertFacts, audience: str) -> list[str]:
    """Problems with a model-written message; empty list means it can be sent."""
    problems = []
    if not text or not text.strip():
        return ["empty"]
    if len(text) > MAX_CHARS[audience]:
        problems.append(f"too long ({len(text)} > {MAX_CHARS[audience]})")
    allowed = facts.allowed_numbers()
    plain = re.sub(r"PM\s?2[.,]5|PM\s?10|N95|µg/m³|ug/m3", " ", text, flags=re.IGNORECASE)
    for n in re.findall(r"\d+(?:[.,]\d+)?", plain):
        if n not in allowed:
            problems.append(f"number not in facts: {n}")
    for pat in FORBIDDEN:
        if re.search(pat, text, flags=re.IGNORECASE):
            problems.append(f"forbidden phrase: {pat}")
    if facts.city.lower() not in text.lower():
        problems.append("city name missing")
    if not re.search(rf"\b{facts.probability_pct}\s?(%|persen)", text):
        problems.append("probability with % missing")
    if facts.pm25_now is not None and not re.search(
            rf"PM\s?2[.,]5[^.]{{0,40}}\b{facts.pm25_now}\s?(µg/m³|ug/m3)", text, flags=re.IGNORECASE):
        problems.append("current PM2.5 with unit missing")
    if audience == "warga" and facts.risk == "TINGGI" and "puskesmas" not in text.lower():
        problems.append("puskesmas advice missing")
    return problems


def parse_messages(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", flags=re.DOTALL)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    return {a: str(data.get(a, "")).strip() for a in AUDIENCES}


@dataclass
class Alert:
    audience: str
    text: str
    source: str            # "muse" or "template"
    problems: list[str]    # why Muse's version was replaced, if it was


def write_alerts(facts: AlertFacts, client=None, model: str = "muse-spark-1.3") -> list[Alert]:
    """No alert for AMAN. Muse writes the messages when a client is given; otherwise templates."""
    if facts.risk == "AMAN":
        return []
    drafts, error = {}, None
    if client is not None:
        prompt = WRITE_PROMPT.format(city=facts.city, facts=json.dumps(asdict(facts), ensure_ascii=False, indent=1))
        try:
            resp = client.responses.create(model=model, temperature=0.3,
                                           input=[{"type": "message", "role": "user",
                                                   "content": [{"type": "input_text", "text": prompt}]}])
            drafts = parse_messages(resp.output_text or "")
        except Exception as exc:  # network or API trouble: fall back to templates
            error = f"Muse call failed: {exc}"
    alerts = []
    for audience in AUDIENCES:
        text = drafts.get(audience, "")
        problems = check_message(text, facts, audience) if client is not None and not error else []
        if client is not None and not error and not problems:
            alerts.append(Alert(audience, f"{text}\n{DISCLAIMER}", "muse", []))
        else:
            why = [error] if error else problems
            alerts.append(Alert(audience, f"{template_message(facts, audience)}\n{DISCLAIMER}", "template", why))
    return alerts
