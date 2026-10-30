# YouTube upload: Jejak Asap demo

Files: `media/jejak_asap_demo_3min.mp4` (2:59, 1280×720, narrated) and `media/jejak_asap_thumbnail.jpg` (1280×720).

## Title (78 characters)

Jejak Asap: Haze Early Warning for Kalimantan with Meta SAM 3.1 and Muse Spark

## Description

Every fire season, haze from burning peat reaches Kalimantan's cities hours before anyone warns the people who live there. Jejak Asap gives each city a 24-hour smoke forecast: the chance that smoke arrives, when it arrives, and what residents, schools and health centres should do.

How it works
• NASA FIRMS fire hotspots and ERA5 wind drive a 20-scenario particle forecast for each city
• Meta SAM 3.1 cuts out smoke plumes on NASA satellite images; Muse Spark checks smoke vs cloud
• Muse Spark writes alerts for residents, school principals and health centres; fixed rules (BMKG air bands, Ministry of Health guidance) choose the actions, and every message is checked before use

What we found, tested on the 2023 fire season against hourly PM2.5
• Wind beats a simple "near a fire" rule out of sample: CSI 0.36 vs 0.29 (0.45 with measured PM2.5)
• Adding SAM-detected smoke did not raise the score over 46 days; we report that too

Built for the Meta Global AI Developer Hackathon (Push the Frontier) by M. Sopian Hadianto.
Live demo: https://jejakasap.mshadianto.id
Code: https://github.com/mshadianto/jejak-asap

Data: NASA FIRMS, NASA GIBS, ERA5 and CAMS via Open-Meteo, BMKG, Kemenkes. Forecasts in this video are model replays of past days, not official warnings.

Chapters
0:00 Haze reaches cities before warnings do
0:14 A real day: 15 October 2023
0:26 Wind forecast in 20 scenarios
0:39 Scored against hourly PM2.5
0:50 SAM 3.1 + Muse Spark find the smoke
1:05 Palangka Raya: forecast vs measured air
1:20 Muse Spark writes the alerts, rules check them
1:51 Sampit and 23 October: the hard cases
2:11 Result: wind beats a distance rule
2:31 Did SAM raise the score? The honest answer
2:42 What Jejak Asap is

## Tags

haze, forest fire, peatland fire, karhutla, Kalimantan, Indonesia, air quality, PM2.5, early warning, wildfire smoke, Meta AI, SAM 3.1, Segment Anything, Muse Spark, hackathon, satellite, NASA FIRMS, AI for good

## Settings

| Setting | Choose |
|---|---|
| Visibility | Unlisted (anyone with the link can watch; switch to Public after judging if you like) |
| Audience | No, it's not made for kids |
| Category | Science & Technology |
| Video language and caption language | English |
| Thumbnail | Upload `jejak_asap_thumbnail.jpg` (needs a verified channel) |
| Comments | On |
| Subtitles | Not needed: they are already on screen |
