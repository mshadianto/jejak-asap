# Jejak Asap: haze early warning for Kalimantan

*Jejak Asap* means "trail of smoke". During the peatland fire season, haze from Kalimantan
reaches cities hours before anyone tells the people who live there. Jejak Asap turns
satellite fire detections into a 24-hour forecast for each city: the chance that smoke
arrives, when it arrives, and what residents, schools and health centres should do about it.

Built for the Meta Global AI Developer Hackathon (indie track, *Push the Frontier*) by
M. Sopian Hadianto.

**Live demo:** https://jejakasap.mshadianto.id · **Demo video (3 min):** https://youtu.be/-k62AoPYIsg

```
NASA FIRMS hotspots ─┐
NASA GIBS true colour ┼─► SAM 3.1 cuts out smoke ─► Muse Spark: smoke or cloud?
ERA5 wind (100 m) ───┘                 │
                                       ▼
          20-member particle ensemble, 24 h (RK2, wind spread ±15°, ±20%)
                                       ▼
          per city: chance of smoke within 25 km, arrival hour, risk level
                                       ▼
   rules pick the actions (BMKG, Kemenkes) ─► Muse Spark writes three messages
                                       ▼
          checks: numbers, forbidden claims, units, length ─► send or fall back
```

## What we found (and what we did not)

Every number below comes from replaying real days of 2023 and scoring each city against hourly
PM2.5. A city counts as smoky when PM2.5 reaches 55.5 µg/m³, where BMKG's "Tidak Sehat"
(unhealthy) band starts. The reference is CAMS model PM2.5 via Open-Meteo, not ground sensors.
CSI = hits ÷ (hits + misses + false alarms); 1 is perfect.

**1. Wind adds skill over a naive distance rule.** We picked settings on September and scored
them on October (13 cities, 16 days).

| Warning rule | POD | FAR | CSI |
|---|---|---|---|
| Jejak Asap, chance ≥ 0.5 | 0.68 | 0.57 | **0.36** |
| No wind: any fire within 25 km | 0.38 | 0.46 | 0.29 |
| PM2.5 at issue time only | 0.38 | 0.00 | 0.38 |
| PM2.5 at issue time + Jejak Asap | 0.62 | 0.38 | **0.45** |
| PM2.5 at issue time + 25 km rule | 0.56 | 0.37 | 0.42 |

The best use is the combination: measured air now, plus the wind forecast to catch the clean
cities that are about to be hit. The forecast also gives an arrival hour; the average error was
5.9 h on the cases where smoke arrived after the forecast was issued.

**2. Seeing the smoke with SAM 3.1 + Muse did not raise the score.** This was a paired test:
the same dates, wind, fires and random seed, with and without the plumes SAM found and Muse accepted.

| Test | Fires + wind | + SAM/Muse plumes | Difference in CSI (95% CI) |
|---|---|---|---|
| Whole region, one 0.5 km/px image, 31 days | 0.33 | 0.34 | +0.01 (−0.03 to +0.05) |
| City boxes at 110 m/px, October, 16 days | 0.43 | 0.47 | +0.045 (+0.01 to +0.09) |
| City boxes, August–September, 30 days | 0.24 | 0.21 | −0.035 (−0.08 to +0.01) |
| City boxes, all 46 days | 0.33 | 0.31 | −0.02 (−0.06 to +0.02) |

October looked like a win, but the August–September rerun did not repeat it. In light-smoke
months SAM finds small real plumes that never push a city above 55.5 µg/m³, so false alarms go
up. Over the whole region, Muse also accepted thin coastal haze on clean days. We kept SAM + Muse
for what they do well, which is showing a person where the smoke is, and we do not claim they
improve the forecast.

**3. Muse writes the alerts, rules decide what they say.** For every city at risk, Muse Spark
writes a WhatsApp message for residents, a memo for school principals and a note for the health
centre (puskesmas). Rules choose the actions: BMKG PM2.5 bands; Kemenkes guidance on vulnerable
groups (infants, toddlers, older adults, pregnant women); and Kemenkes advice that N95 masks are
only for healthy adults who can get a proper fit. Each message must:

- use only numbers and hours present in the forecast;
- avoid forbidden claims ("pasti", "dijamin", closing schools, evacuation, drug names);
- name the city, the chance with %, and the current PM2.5 with its unit;
- stay under a length limit.

If any check fails, a plain template goes out instead. Every message says it is a model forecast,
not an official BMKG/BPBD notice. Unhealthy air measured now overrides a milder forecast. In
Sampit on 15 Oct 2023 the forecast said SEDANG while PM2.5 was 190 µg/m³, so the TINGGI actions
were used.

Across 5 October days the run produced 165 messages, and all of them passed the checks. The checks
also caught real problems along the way: an early version let "peluang 70" through without the %,
and another left out the current PM2.5. The rules were tightened in both cases. On 23 Oct the
forecast still raised TINGGI for two cities on a clean day. Those are real false alarms, and the
demo shows them.

## Built during the hackathon (26 Oct – 4 Nov 2026)

Everything in this repository was written during the hackathon: the SAM 3.1 + Muse smoke pipeline,
the particle forecast, the backtests, the alert writer, the demo page and the video. Data come from
public sources (NASA, ERA5, CAMS, BMKG, Kemenkes guidance).

Jejak Asap does not give medical advice. The health actions are fixed public-health guidance from
Indonesia's Ministry of Health and BMKG air-quality bands, chosen by rules; Muse Spark only words them,
and every message is checked and labelled as a model forecast, not an official notice.

## Meta models used

| Model | Where | Why |
|---|---|---|
| `sam-3.1` | `jejak_asap/sam.py` | Point prompts placed a few km downwind of each fire cluster (up to 16 objects, sent in batches of 4); masks become plume polygons. |
| `muse-spark-1.3` (image) | `jejak_asap/muse.py` | Looks at each SAM mask drawn on the image and says smoke, cloud, mixed or other, with a smoke fraction. |
| `muse-spark-1.3` (text) | `jejak_asap/alerts.py` | Writes the three audience messages from fixed facts and actions; checked before use. |

Calls go through the Responses API at `https://api.meta.ai/v1` with the OpenAI SDK. Masks are
decoded with the official `meta-sam-parser`. Only satellite images and forecast numbers are sent;
no personal data.

## Run it

```bash
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env    # MODEL_API_KEY (Meta), FIRMS_MAP_KEY (free from NASA FIRMS)
python -m pytest -q     # 39 tests

# Stage 1: fires -> SAM 3.1 -> Muse -> smoke polygons (a 1.3 x 1.1 degree box, 14 Sep 2019)
python run_stage1.py --date 2019-09-14 --bbox 113.3,-3.6,114.6,-2.5 --width 1300
# Stage 2: 24 h particle forecast and city risk (GIF + map)
python run_stage2.py --stage1 out/2019-09-14_113.3_-3.6_114.6_-2.5
# Backtest: 31 days of Sep-Oct 2023, fires + wind vs distance and persistence rules
python run_series.py --start 2023-09-01 --end 2023-10-31 --step-days 2
# Paired SAM test on 1.2 degree city boxes (Windows helper for stage 1 on many dates)
powershell -File run_local_boxes.ps1 -Start 2023-10-01 -End 2023-10-31
python run_series.py --start 2023-10-01 --end 2023-10-31 --step-days 2 --plumes-root out/plumes_local --out out/series_sam_local
python compare_sam.py --fires out/series --sam out/series_sam_local --cities "Palangka Raya,Pulang Pisau,Kasongan,Banjarmasin,Banjarbaru,Kuala Kapuas,Sampit"
# The August-September rerun
powershell -File run_local_boxes.ps1 -Start 2023-08-02 -End 2023-09-29
python run_series.py --start 2023-08-02 --end 2023-09-29 --step-days 2 --plumes-root out/plumes_local --out out/series_sam_local_rep
# Alerts for every city at risk on one day (templates only: add --no-muse)
python run_alerts.py --forecast out/series/2023-10-15.json
```

Long runs can be resumed: `--resume` and `--time-budget` (exit code 3 means "run again").

## Repository

| Path | What it does |
|---|---|
| `jejak_asap/firms.py`, `imagery.py`, `wind.py` | FIRMS hotspots and clustering, GIBS images, Open-Meteo wind |
| `jejak_asap/sam.py`, `muse.py`, `pipeline.py` | Stage 1: SAM prompts, Muse checks, polygons, caching |
| `jejak_asap/transport.py`, `impact.py` | Stage 2: wind grid, particle ensemble, city scoring, maps |
| `jejak_asap/backtest.py`, `run_series.py`, `compare_sam.py` | Scoring against PM2.5, baselines, bootstrap, paired tests |
| `jejak_asap/alerts.py`, `run_alerts.py` | Rule-based actions, Muse messages, safety checks |
| `docs/CATATAN_ID.md` | Full working notes in Indonesian, including every intermediate result |

## Limits

- The PM2.5 reference is a model (CAMS); Grosvenor et al. 2024 (doi:10.1038/s43247-024-01813-w)
  found it tracks low-cost sensors in Palangka Raya in 2019, but sensor data would be better.
- Fires are daytime VIIRS detections only; smouldering peat at night is missed, and peat and
  non-peat fires get the same emission weight. These are the likeliest causes of the high false
  alarm rate.
- 13 cities, one province pair, one fire season. All tests are retrospective, run on 2023 data.
- Alerts are drafts for a duty officer, not automatic broadcasts.

## Data sources

NASA FIRMS (VIIRS S-NPP hotspots), NASA GIBS (VIIRS true colour), ERA5 via Open-Meteo archive
(100 m wind), CAMS via Open-Meteo Air Quality (PM2.5), BMKG PM2.5 categories, Kemenkes haze and
mask guidance (15 Oct 2026; 15 Oct 2015).
