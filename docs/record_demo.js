const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1,
    recordVideo: { dir: '/tmp/claude-0/video/raw', size: { width: 1280, height: 720 } }, colorScheme: 'light' });
  const page = await ctx.newPage();
  await page.goto('http://localhost:8765/_demo.html' /* wrap index.html in a full HTML document as _demo.html first */ + '');
  await page.waitForTimeout(2500);
  await page.addStyleTag({ content: `
    #cap { position: fixed; left: 50%; bottom: 22px; transform: translateX(-50%); z-index: 9999; max-width: 1040px;
      background: rgba(16,22,19,.92); color: #fff; font: 600 22px/1.35 "Source Sans 3", sans-serif; padding: 14px 22px;
      border-radius: 8px; box-shadow: 0 6px 24px rgba(0,0,0,.35); text-align: center; transition: opacity .3s; }
    #cap b { color: #ff9a5c; }
    #card { position: fixed; inset: 0; z-index: 10000; background: #111614; color: #e4ebe7; display: flex;
      flex-direction: column; justify-content: center; padding: 0 120px; gap: 18px; font-family: "Source Sans 3", sans-serif; }
    #card h1 { font: 800 72px/1 Archivo, sans-serif; font-stretch: 78%; margin: 0; color: #fff; }
    #card p { font-size: 28px; line-height: 1.4; margin: 0; max-width: 980px; }
    #card .k { font: 600 16px/1 "JetBrains Mono", monospace; letter-spacing: .14em; text-transform: uppercase; color: #ff7a3d; }
    .hl { outline: 4px solid #ff7a3d !important; outline-offset: 4px; border-radius: 6px; }
  `});
  const cap = async (html, ms) => {
    await page.evaluate(h => { let c = document.getElementById('cap'); if (!c) { c = document.createElement('div'); c.id = 'cap'; document.body.appendChild(c); } c.innerHTML = h; c.style.opacity = 1; }, html);
    await page.waitForTimeout(ms);
  };
  const card = async (html, ms) => {
    await page.evaluate(h => { let c = document.getElementById('card'); if (!c) { c = document.createElement('div'); c.id = 'card'; document.body.appendChild(c); } c.innerHTML = h; c.style.display = 'flex'; }, html);
    await page.waitForTimeout(ms);
    await page.evaluate(() => { const c = document.getElementById('card'); if (c) c.style.display = 'none'; });
  };
  const hl = async (sel, on = true) => page.evaluate(([s, o]) => { document.querySelectorAll('.hl').forEach(e => e.classList.remove('hl')); if (o && s) { const e = document.querySelector(s); if (e) e.classList.add('hl'); } }, [sel, on]);
  const scrollTo = async y => { await page.evaluate(y => window.scrollTo({ top: y, behavior: 'smooth' }), y); await page.waitForTimeout(900); };
  const tally = async () => page.evaluate(() => [...document.querySelectorAll('#tally b')].map(b => b.textContent));
  const clickCity = async name => { await page.locator('#cities button', { hasText: name }).first().click(); await page.waitForTimeout(500); };
  const clickDate = async dd => { await page.locator('#dates button', { hasText: dd }).first().click(); await page.waitForTimeout(700); };

  await card(`<div class="k">Meta AI Hackathon · Push the Frontier</div><h1>Jejak Asap</h1>
    <p>Every fire season, haze from burning peat reaches Kalimantan's cities hours before anyone warns the people who live there.</p>
    <p style="color:#9aaaa2">A 24-hour smoke forecast per city, alerts written by Muse Spark, and an honest backtest.</p>`, 9000);

  await scrollTo(150);
  await cap('15 October 2023. Yellow dots are <b>NASA FIRMS fire clusters</b> on the real VIIRS satellite image of the day.', 7000);
  await cap('Wind from ERA5 carries smoke particles for 24 hours in <b>20 scenarios</b>. Each city gets a chance of smoke and an arrival hour.', 8000);
  await hl('.side .panel');
  const t = await tally();
  await cap(`Every forecast is scored against hourly PM2.5. This day: <b>${t[0]} hit${t[0] === "1" ? "" : "s"}, ${t[1]} miss${t[1] === "1" ? "" : "es"}, ${t[2]} false alarm${t[2] === "1" ? "" : "s"}</b>.`, 7000);
  await hl(null, false);

  await page.click('#mSam'); await page.waitForTimeout(600);
  await cap('<b>SAM 3.1</b> cuts out the smoke around each fire; <b>Muse Spark</b> checks every cut: smoke, cloud, or water?', 8000);
  await cap('Orange areas are the plumes Muse accepted. Here 5 were accepted and 6 rejected as cloud or sea.', 6500);
  await page.click('#mFire'); await page.waitForTimeout(500);

  await clickCity('Palangka Raya');
  await scrollTo(380);
  await hl('#detail');
  await cap('Palangka Raya: the bars are the forecast chance by hour, the line is measured PM2.5. Already unhealthy at issue time.', 8000);
  await scrollTo(760);
  await cap('<b>Muse Spark writes the alerts</b>: one for residents, one for school principals, one for the health centre.', 7000);
  await page.locator('.tabs button', { hasText: 'School principal' }).click(); await page.waitForTimeout(3500);
  await page.locator('.tabs button', { hasText: 'Health centre' }).click(); await page.waitForTimeout(3500);
  await cap('Rules pick the actions, from BMKG air bands and Ministry of Health guidance. Muse only writes the words.', 7500);
  await cap('Each message is checked: <b>no invented numbers</b>, no promises, no "schools closed", units required. Fail = plain template.', 8500);
  await hl(null, false);

  await scrollTo(150);
  await clickCity('Sampit');
  await scrollTo(760);
  await hl('#detail');
  await cap('Sampit: the forecast said SEDANG, but measured PM2.5 was <b>190 µg/m³</b>. Measured air overrides the model, so the strongest advice goes out.', 9000);
  await hl(null, false);

  await scrollTo(150);
  await clickDate('23');
  const t2 = await tally();
  await hl('.side .panel');
  await cap(`23 October was a clean day, yet the forecast raised <b>${t2[2]} false alarms</b>. We show the misses too.`, 8000);
  await hl(null, false);

  const ev = await page.evaluate(() => document.querySelector('#evidence').getBoundingClientRect().top + window.scrollY - 70);
  await scrollTo(ev);
  await hl('#evidence > div:nth-child(1)');
  await cap('Out of sample, wind beats a "near the fire" rule: <b>CSI 0.36 vs 0.29</b>. With measured PM2.5 added: 0.45.', 9000);
  await hl('#evidence > div:nth-child(3)');
  await cap('Did seeing the smoke with SAM help the score? October said yes; the August-September rerun said <b>no</b>. We report both.', 9500);
  await hl(null, false);

  await card(`<div class="k">What Jejak Asap is</div><h1>Know when the haze arrives.</h1>
    <p>Fires + wind give each city a chance of smoke and an arrival hour. Measured air keeps it honest.</p>
    <p>SAM 3.1 and Muse Spark show where the smoke is and turn the forecast into clear, checked messages for residents, schools and health centres.</p>
    <p style="color:#9aaaa2;font-size:22px">Built by M. Sopian Hadianto · NASA FIRMS, NASA GIBS, ERA5, CAMS · Meta SAM 3.1 + Muse Spark 1.3</p>`, 11000);

  await ctx.close();
  await browser.close();
})();
