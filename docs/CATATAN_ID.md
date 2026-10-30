# Jejak Asap — tahap 1: titik api → SAM 3.1 → Muse → polygon asap

Alur: citra true-colour NASA GIBS + titik api NASA FIRMS → titik api digugus (maks. 16 gugus) →
titik prompt digeser searah angin (Open-Meteo) → **SAM 3.1** (point prompt) memotong asap →
**Muse Spark 1.3** memeriksa tiap potongan: asap atau awan → GeoJSON + gambar overlay.

## Pasang (Windows PowerShell)

```powershell
cd jejak-asap
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # lalu isi MODEL_API_KEY dan FIRMS_MAP_KEY di .env
```

`FIRMS_MAP_KEY` gratis: https://firms.modaps.eosdis.nasa.gov/api/map_key/ .
Jangan pernah commit file `.env`.

## Jalankan

```powershell
# Kasus uji: pesisir selatan Kalimantan Tengah, 14 Sep 2019
python run_stage1.py --date 2019-09-14 --bbox 113.3,-3.6,114.6,-2.5 --width 1300

# Tanpa memanggil Meta (cek titik prompt dulu, gratis)
python run_stage1.py --date 2019-09-14 --bbox 113.3,-3.6,114.6,-2.5 --dry-run

# Angin manual bila Open-Meteo sedang membatasi (arah DARI mana angin bertiup)
python run_stage1.py --date 2019-09-14 --bbox 113.3,-3.6,114.6,-2.5 --wind-from 160 --wind-kmh 15
```

Setiap tahap menyimpan hasilnya di folder `out/`. Kalau proses terputus, ulangi perintah yang sama
dengan `--resume` dan pipeline melanjutkan dari tahap terakhir tanpa memanggil API lagi.
`--time-budget 35` membuatnya berhenti rapi di antara tahap (exit code 3 = belum selesai, ulangi).

Opsi lain: `--no-muse`, `--offset-km 0` (titik tepat di api), `--hotspots-csv file.csv`,
`--firms-source VIIRS_SNPP_NRT` (data terbaru). Tanpa `FIRMS_MAP_KEY`, titik api dibaca dari
lapisan gambar GIBS: posisinya perkiraan, tanpa FRP dan jam (hanya untuk demo).

## Keluaran (`out/<tanggal>_<bbox>/`)

| File | Isi |
|---|---|
| `image.jpg` | citra yang dikirim ke SAM |
| `sam_prompt.txt` | point prompt yang dikirim |
| `sam_raw.txt` | keluaran mentah SAM |
| `plumes.geojson` | polygon asap (diterima/ditolak Muse) + titik gugus api |
| `overlay.png` | hijau = asap diterima, abu-abu = ditolak, kuning = titik prompt, merah = titik api |
| `report.json` | ringkasan: jumlah gugus, plume diterima, luas km² |

## Uji

```powershell
python -m pytest -q
```

## Batasan yang diketahui

- Gumpalan asap yang menyambung bisa tergabung jadi satu objek SAM.
- Tepi potongan bisa bocor ke awan kecil; saringan Muse dan titik negatif menanganinya.
- Angin 10 m dari ERA5 hanya perkiraan arah asap; ini bukan model dispersi.
- Membaca mask "lossless" lambat (parser resmi Meta berbasis Python murni): sekitar 30 detik
  untuk 10 objek pada citra 1300×1100. Hasilnya disimpan di `objects.npz` sehingga cukup sekali.
- Mask yang menutupi lebih dari 50% gambar langsung ditolak (biasanya titik jatuh di kabut merata).

## Hasil uji 14 Sep 2019 (pesisir selatan Kalimantan Tengah)

1.569 titik api FIRMS (770 siang hari) → 12 gugus → SAM 10 objek → 5 gumpalan asap diterima
(±4.888 km²). Muse menolak 2 objek awan dan 2 objek laut keruh; gerbang ukuran menolak 1 objek.
Angin ERA5 05 UTC: dari 166°, 13,4 km/jam.

# Tahap 2: lintasan asap 24 jam dan risiko per kota

```powershell
python run_stage2.py --stage1 out/2019-09-14_113.3_-3.6_114.6_-2.5
```

Partikel dilepas dari polygon asap yang diterima Muse (asap yang sudah ada) dan, tiap jam, dari
gugus api (api terus menyala). Partikel digerakkan angin 100 m per jam (Open-Meteo; `archive` =
ERA5 untuk backtest, `forecast` = prakiraan untuk operasional). 20 skenario angin (arah ±15°,
kecepatan ±20%) memberi **peluang** asap tiba, bukan satu garis pasti.

Keluaran di `stage2_<sumber>_<ketinggian>/`: `impact.json` (per kota: risiko, peluang, jam tiba,
jam puncak, lama terdampak), `forecast_map.png`, `forecast.gif` (animasi 24 jam).

Aturan risiko (deterministik, belum dikalibrasi): kota terdampak bila ada partikel dalam radius
25 km. TINGGI = peluang ≥60% dan porsi asap ≥2%; SEDANG = peluang ≥30%; RENDAH = peluang >0%.
Ini memprakirakan arah dan waktu, bukan konsentrasi PM2.5.

Hasil 14 Sep 2019 (angin ERA5 100 m): Palangka Raya diperkirakan terkena pukul 13.00 WIB
(peluang 100%), Banjarmasin aman (hulu angin).

# Backtest: prakiraan vs PM2.5 per jam

```powershell
python run_backtest.py --stage2 out/2023-10-13_113.3_-3.6_114.9_-2.0/stage2_archive_100m
```

Rujukan bawaan: PM2.5 CAMS per jam dari Open-Meteo Air Quality API (terisi untuk 2023, kosong untuk
2019 saat dicek). CAMS adalah model, bukan sensor; Grosvenor dkk. (2024,
doi:10.1038/s43247-024-01813-w) menunjukkan CAMS sejalan dengan sensor lapangan di Palangka Raya 2019.
Data sensor sendiri bisa dipakai lewat `--obs-csv` (kolom `city,time_utc,pm25`).
"Asap tiba" = PM2.5 ≥ 55,5 µg/m³. Keluaran: `backtest.json`, `backtest.png`, plus pembanding tanpa
angin ("kota dalam radius R km dari api = kena asap") yang harus dikalahkan model.

Hasil 13 Okt 2023 (satu kasus): POD 0,75, FAR 0,14, CSI 0,67. Pembanding radius 100 km juga CSI 0,67
dan radius 150 km CSI 0,89: pada hari kabut regional ini model belum lebih baik dari aturan sederhana.
Perlu backtest beberapa hari (termasuk hari bersih) dan sumber api di luar kotak analisis.

# Backtest banyak hari (hanya dari titik api)

```powershell
python run_series.py --start 2023-09-01 --end 2023-10-31 --step-days 2 --time-budget 34
```

Tiap tanggal: titik api FIRMS se-Kalteng-Kalsel (110,5–117,5 BT, 4,5 LS–0,5 LU) → gugus api →
12 jam pemanasan + 24 jam lintasan partikel dengan angin ERA5 → 13 kota dinilai terhadap PM2.5 CAMS.
Tanpa SAM dan Muse (murah, bisa diulang untuk banyak hari). Hasil per tanggal disimpan di
`out/series/`, ringkasan di `summary.json` dan `summary.png`.

Hasil 31 hari (1 Sep–31 Okt 2023, selang 2 hari), 403 kota-hari, 110 berasap:

| Aturan peringatan | POD | FAR | CSI |
|---|---|---|---|
| Jejak Asap, peluang ≥ 0,5 | 0,74 | 0,62 | 0,33 |
| Tanpa angin, radius 50 km (terbaik) | 0,70 | 0,65 | 0,31 |
| PM2.5 jam awal saja (persistensi) | 0,34 | 0,00 | 0,34 |
| PM2.5 jam awal + radius 25 km | 0,58 | 0,46 | 0,39 |
| PM2.5 jam awal + Jejak Asap ≥ 0,9 | 0,59 | 0,40 | 0,42 |

Bootstrap per hari (2.000 kali): Jejak Asap vs radius 50 km +0,03 CSI (95% CI −0,03 s.d. +0,08);
gabungan dengan PM2.5 jam awal vs persistensi saja +0,09 (95% CI 0,00 s.d. +0,17).
Ambang dipilih pada data yang sama, jadi angka ini cenderung optimistis.

Uji jujur dengan data terpisah (setelan dipilih dari tanggal September, dinilai pada tanggal Oktober):

| Aturan (setelan dari September) | POD Okt | FAR Okt | CSI Okt |
|---|---|---|---|
| Jejak Asap, peluang ≥ 0,5 | 0,68 | 0,57 | 0,36 |
| Tanpa angin, radius 25 km | 0,38 | 0,46 | 0,29 |
| PM2.5 jam awal saja | 0,38 | 0,00 | 0,38 |
| PM2.5 jam awal + radius 25 km | 0,56 | 0,37 | 0,42 |
| PM2.5 jam awal + Jejak Asap ≥ 0,9 | 0,62 | 0,38 | 0,45 |

Artinya, dengan jujur: hanya dari titik api, lintasan angin baru sedikit lebih baik dari aturan jarak
dan belum terbukti secara statistik. Nilai tambah yang lebih jelas muncul bila digabung dengan PM2.5
yang sedang terukur: model menangkap kota yang akan terkena asap padahal saat ini masih bersih.
Kelebihan lain yang tidak terukur oleh CSI: model memberi jam tiba (galat rata-rata 5,9 jam) dan arah,
sedangkan aturan jarak memperingatkan semua kota di sekitar api.
Penyebab FAR tinggi yang diduga: titik api siang hari saja (gambut membara malam hari), emisi per gugus
belum dibedakan gambut/non-gambut, dan PM2.5 rujukan berupa model CAMS beresolusi kasar.

# Uji berpasangan: apakah asap dari SAM 3.1 + Muse memperbaiki prakiraan?

```powershell
# tahap 1 di seluruh domain untuk tiap tanggal (sekitar 2-3 menit per tanggal)
python run_stage1.py --date 2023-10-13 --bbox 110.5,-4.5,117.5,0.5 --width 1400 --out out/plumes --resume
# seri yang sama, ditambah asap yang diterima Muse pada jam analisis
python run_series.py --start 2023-09-01 --end 2023-10-31 --step-days 2 --plumes-root out/plumes --out out/series_sam
python compare_sam.py --fires out/series --sam out/series_sam
```

Tanggal, angin, titik api, skenario dan benih acak sama persis; bedanya hanya gumpalan asap yang
ditemukan SAM 3.1 dan diterima Muse Spark 1.3. Ambang peringatan 0,5 dibekukan dari seri titik api saja.

| 31 tanggal, 403 kota-hari | POD | FAR | CSI |
|---|---|---|---|
| Titik api saja | 0,74 | 0,62 | 0,33 |
| Titik api + asap SAM/Muse (smoke + mixed) | 0,85 | 0,64 | 0,34 |
| Titik api + asap SAM/Muse (hanya label smoke) | 0,76 | 0,64 | 0,32 |

Selisih CSI +0,01 (95% CI −0,03 s.d. +0,05): **tidak ada perbaikan yang berarti pada skala regional.**
Asap dari citra membantu pada hari kabut luas (3 Okt CSI 0,50 → 0,83; 15 Okt 0,62 → 0,78) tetapi
merugikan saat Muse menerima kabut tipis di atas laut pada hari yang menurut CAMS bersih (21 dan
23 Okt, ±30.000 km² diterima). Satu citra 7°×5° pada ±0,5 km/piksel terlalu kasar untuk point prompt
per gumpalan; SAM sering memotong "selimut" kabut atau perairan keruh. Label Muse juga tidak
sepenuhnya stabil (tanggal yang sama dijalankan dua kali memberi 45.597 vs 26.670 km² diterima).
Keluaran: `out/series_sam/compare.json` dan `compare.png`.

## Uji SAM lokal: kotak 1,2° di sekitar kota (±110 m/piksel)

```powershell
powershell -ExecutionPolicy Bypass -File run_local_boxes.ps1   # 3 kotak x 16 tanggal Oktober 2023
python run_series.py --start 2023-10-01 --end 2023-10-31 --step-days 2 --plumes-root out/plumes_local --out out/series_sam_local
python compare_sam.py --fires out/series --sam out/series_sam_local --cities "Palangka Raya,Pulang Pisau,Kasongan,Banjarmasin,Banjarbaru,Kuala Kapuas,Sampit"
```

Kotak: Palangka Raya (113,3–114,5 BT, 2,9–1,7 LS), Banjarmasin (114,0–115,2 BT, 3,8–2,6 LS),
Sampit (112,35–113,55 BT, 3,13–1,93 LS). Dinilai hanya 7 kota di dalam kotak, 16 tanggal, 112 kota-hari.
Ukuran kotak dan ambang 0,5 ditetapkan sebelum melihat hasil.

| 7 kota, 16 tanggal Okt 2023 | POD | FAR | CSI |
|---|---|---|---|
| Titik api saja | 0,68 | 0,46 | 0,43 |
| Titik api + asap SAM/Muse lokal | 0,77 | 0,45 | 0,47 |
| PM2.5 jam awal + titik api | 0,66 | 0,29 | 0,52 |
| PM2.5 jam awal + titik api + asap SAM/Muse lokal | 0,77 | 0,28 | 0,59 |

Selisih CSI +0,045 (95% CI +0,01 s.d. +0,09, bootstrap per hari): perbaikan kecil tapi konsisten.
Tidak ada satu tanggal pun yang memburuk; empat tanggal membaik (1, 3, 5 dan 15 Okt). Pada hari
bersih (21–27 Okt) SAM/Muse lokal tidak menerima asap sama sekali, berbeda dengan versi regional.
Sebagai pembanding, versi regional pada 16 tanggal dan 7 kota yang sama: +0,06 tetapi 95% CI
−0,02 s.d. +0,15 dan ada hari yang memburuk. Catatan jujur: ini uji ketiga atas SAM (regional,
regional label smoke saja, lokal), sampel kecil, dan rujukan PM2.5 masih CAMS; perlu diulang pada
musim kebakaran lain (mis. 2019) dengan data sensor sebelum dijadikan klaim kuat.

### Replikasi Agustus–September 2023: hasil Oktober tidak terulang

```powershell
powershell -ExecutionPolicy Bypass -File run_local_boxes.ps1 -Start 2023-08-02 -End 2023-09-29
python run_series.py --start 2023-08-02 --end 2023-09-29 --step-days 2 --plumes-root out/plumes_local --out out/series_sam_local_rep
python compare_sam.py --fires out/series --sam out/series_sam_local_rep --cities "Palangka Raya,Pulang Pisau,Kasongan,Banjarmasin,Banjarbaru,Kuala Kapuas,Sampit"
```

| 7 kota dalam kotak | Titik api saja CSI | + SAM/Muse lokal CSI | Selisih (95% CI) |
|---|---|---|---|
| Oktober, 16 tanggal | 0,43 | 0,47 | +0,045 (+0,01 s.d. +0,09) |
| Agustus–September, 30 tanggal | 0,24 | 0,21 | −0,035 (−0,08 s.d. +0,01) |
| Gabungan, 46 tanggal | 0,33 | 0,31 | −0,016 (−0,06 s.d. +0,02) |

Kesimpulan: **SAM 3.1 + Muse tidak terbukti menaikkan skor prakiraan kota.** Pada bulan kabut berat
(Oktober) ada perbaikan, pada bulan asap tipis (Agustus–September) gumpalan kecil yang ditemukan SAM
menambah alarm palsu karena PM2.5 kota tidak sampai 55,5 µg/m³. Peran SAM + Muse di Jejak Asap: bukti visual gumpalan asap untuk dibaca manusia.
Skor prakiraan datang dari lintasan angin + PM2.5 terukur.

# Peringatan per audiens (Muse Spark menulis, aturan yang menentukan)

```powershell
python run_alerts.py --forecast out/series/2023-10-15.json                 # semua kota berisiko
python run_alerts.py --forecast out/series/2023-10-15.json --city Sampit --no-muse
```

Tiga pesan per kota: **warga** (WhatsApp), **sekolah** (memo kepala sekolah), **puskesmas** (catatan
operasional). Tindakan dipilih oleh aturan tetap (`jejak_asap/alerts.py`) yang bersumber dari kategori PM2.5
BMKG dan imbauan Kemenkes (kelompok rentan: bayi, balita, lansia, ibu hamil; masker N95 hanya untuk
orang dewasa sehat yang pas memakainya). Muse Spark 1.3 hanya merangkai fakta itu menjadi kalimat.

Pagar pengaman sebelum pesan dipakai:
- semua angka dan jam di pesan harus berasal dari fakta prakiraan;
- kalimat terlarang ditolak (mis. "pasti", "dijamin", "sekolah diliburkan", evakuasi, nama obat);
- nama kota, peluang, dan PM2.5 saat ini beserta satuannya wajib ada; panjang dibatasi;
- bila udara sudah ≥55,5 µg/m³ saat prakiraan dibuat, tindakan TINGGI dipakai walau model bilang lebih ringan
  (terjadi di Sampit 15 Okt 2023: prakiraan SEDANG, PM2.5 terukur 190);
- bila salah satu gagal, pesan diganti template polos; tiap pesan diberi catatan bahwa ini prakiraan model,
  bukan informasi resmi BMKG/BPBD.

Keluaran: `alerts_<tanggal>.json` dan `.md`.
