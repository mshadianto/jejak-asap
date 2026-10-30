# Demo video: voiceover script (2 min 24 s)

The screen recording `media/jejak_asap_demo.mp4` already carries English captions. Read this script
over it, or record your own voice and replace the captions. Timings follow the recording.

| Time | On screen | Voiceover |
|---|---|---|
| 0:00–0:09 | Title card | Every fire season, haze from burning peat reaches Kalimantan's cities hours before anyone warns the people who live there. Jejak Asap is a 24-hour smoke forecast for each city. |
| 0:09–0:16 | Map, 15 Oct 2023 | This is a real day: 15 October 2023. The yellow dots are fire clusters from NASA FIRMS, on that day's satellite image. |
| 0:16–0:24 | Map | We move smoke particles with ERA5 wind for 24 hours, in 20 slightly different wind scenarios. Each city gets a chance of smoke and an arrival hour. |
| 0:24–0:31 | City list | And we score every forecast against hourly PM2.5: hits, misses and false alarms. |
| 0:31–0:46 | Toggle to SAM | SAM 3.1 cuts out the smoke around each fire. Muse Spark looks at every cut and decides: smoke, cloud, or water. The orange areas are the plumes it accepted. |
| 0:46–0:54 | Palangka Raya chart | For Palangka Raya the bars are our forecast hour by hour, and the line is measured PM2.5. The air was already unhealthy when the forecast was issued. |
| 0:54–1:09 | Alert tabs | Muse Spark then writes three messages: for residents on WhatsApp, for school principals, and for the health centre. |
| 1:09–1:25 | Alert text | The actions are not invented by the model. Fixed rules pick them, from BMKG air-quality bands and Ministry of Health guidance. Every message is checked: no numbers we did not give, no promises, no "schools are closed", units required. If a check fails, a plain template goes out instead. |
| 1:25–1:34 | Sampit | In Sampit the forecast said moderate, but measured PM2.5 was 190. Measured air overrides the model, so the strongest advice goes out. |
| 1:34–1:42 | 23 October | 23 October was a clean day, and the forecast still raised false alarms. We show those too. |
| 1:42–1:51 | Evidence card 1 | Tested on October with settings chosen in September, the wind forecast beats a simple "near a fire" rule: 0.36 against 0.29. Add measured PM2.5 and it reaches 0.45. |
| 1:51–2:01 | Evidence card 3 | Did seeing the smoke with SAM raise the score? October said yes. The August–September rerun said no. We report both. |
| 2:01–2:24 | Closing card | Fires and wind tell each city when the haze arrives. SAM 3.1 and Muse Spark show where the smoke is, and turn the forecast into clear, checked messages for the people who need them. |

## Re-recording (page source in `web/`)

```bash
python -m http.server 8765        # in the folder holding index.html, data.json, img/
node docs/record_demo.js          # Playwright, 1280x720, writes a .webm
ffmpeg -i raw/*.webm -c:v libx264 -pix_fmt yuv420p -crf 23 -movflags +faststart jejak_asap_demo.mp4
```
