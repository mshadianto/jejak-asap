const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1,
    recordVideo: { dir: '/tmp/claude-0/video/raw', size: { width: 1280, height: 720 } }, colorScheme: 'light' });
  const page = await ctx.newPage();
  const rec0 = Date.now();
  await page.goto('http://localhost:8765/_demo.html');
  await page.waitForTimeout(2500);
  await page.addStyleTag({ content: `
    #cap { position: fixed; left: 50%; bottom: 22px; transform: translateX(-50%); z-index: 10001; max-width: 1040px;
      background: rgba(16,22,19,.92); color: #fff; font: 600 21px/1.35 "Source Sans 3", sans-serif; padding: 14px 22px;
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


  // Narration timeline (seconds in Sopian's recording).
  let T0 = 0;
  const at = async t => { const wait = T0 + t * 1000 - Date.now(); if (wait > 0) await page.waitForTimeout(wait); };
  const say = async (t, html) => { await at(t); await page.evaluate(h => { let c = document.getElementById('cap'); if (!c) { c = document.createElement('div'); c.id = 'cap'; document.body.appendChild(c); } c.innerHTML = h; }, html); };
  const showCard = html => page.evaluate(h => { let c = document.getElementById('card'); if (!c) { c = document.createElement('div'); c.id = 'card'; document.body.appendChild(c); } c.innerHTML = h; c.style.display = 'flex'; }, html);
  const hideCard = () => page.evaluate(() => { const c = document.getElementById('card'); if (c) c.style.display = 'none'; });

  await showCard(`<div class="k">Meta AI Hackathon · Push the Frontier</div><h1>Jejak Asap</h1>
    <p>Every fire season, haze from burning peat reaches Kalimantan's cities hours before anyone warns the people who live there.</p>
    <p style="color:#9aaaa2">A 24-hour smoke forecast per city, alerts written by Muse Spark, and an honest backtest.</p>`);
  T0 = Date.now();
  console.log('OFFSET', (T0 - rec0) / 1000);
  await say(0, "Every fire season, haze from burning peat reaches Kalimantan's cities hours before anyone warns the people who live there.");
  await say(11, "Jejak Asap is a 24-hour smoke forecast for each city.");

  await at(16); await hideCard(); await scrollTo(150);
  await say(16.2, "This is a real day: 15 October 2023. The yellow dots are fire clusters from <b>NASA FIRMS</b>, on that day's satellite image.");
  await say(29, "We move smoke particles with ERA5 wind for 24 hours, in <b>20 slightly different wind scenarios</b>. Each city gets a chance of smoke and an arrival hour.");
  await at(43); await hl('.side .panel');
  await say(43, "And we score every forecast against hourly PM2.5: <b>hits, misses and false alarms</b>.");

  await at(54.8); await hl(null, false); await page.click('#mSam');
  await say(55, "<b>SAM 3.1</b> cuts out the smoke around each fire. <b>Muse Spark</b> looks at every cut and decides: smoke, cloud, or water.");
  await say(67, "The orange areas are the plumes it accepted.");

  await at(71); await page.click('#mFire'); await clickCity('Palangka Raya'); await scrollTo(380); await hl('#detail');
  await say(71.5, "For Palangka Raya the bars are our forecast hour by hour, and the line is measured PM2.5. The air was already unhealthy when the forecast was issued.");

  await at(87); await scrollTo(760);
  await say(87.3, "<b>Muse Spark</b> then writes three messages: for residents on WhatsApp, for school principals, and for the health centre.");
  await at(92); await page.locator('.tabs button', { hasText: 'School principal' }).click();
  await at(95); await page.locator('.tabs button', { hasText: 'Health centre' }).click();
  await say(97, "The actions are not invented by the model. Fixed rules pick them, from BMKG air-quality bands and Ministry of Health guidance.");
  await at(103); await page.locator('.tabs button', { hasText: 'Residents' }).click();
  await say(107, "Every message is checked: <b>no numbers we did not give, no promises, no \"schools are closed\"</b>, units required. If a check fails, a plain template goes out instead.");

  await at(121); await hl(null, false); await scrollTo(150); await clickCity('Sampit'); await scrollTo(760); await hl('#detail');
  await say(121.3, "In Sampit the forecast said moderate, but measured PM2.5 was <b>190 µg/m³</b>. Measured air overrides the model, so the strongest advice goes out.");

  await at(135); await hl(null, false); await scrollTo(150); await clickDate('23'); await hl('.side .panel');
  await say(135.3, "23 October was a clean day, and the forecast still raised false alarms. <b>We show those too.</b>");

  await at(143); await hl(null, false);
  const ev = await page.evaluate(() => document.querySelector('#evidence').getBoundingClientRect().top + window.scrollY - 70);
  await scrollTo(ev); await hl('#evidence > div:nth-child(1)');
  await say(143.5, "Tested on October with settings chosen in September, the wind forecast beats a simple \"near a fire\" rule: <b>0.36 against 0.29</b>. Add measured PM2.5 and it reaches <b>0.45</b>.");
  await at(164); await hl('#evidence > div:nth-child(3)');
  await say(164, "Did seeing the smoke with SAM raise the score? October said yes. The August–September rerun said <b>no</b>. We report both.");

  await at(176); await hl(null, false);
  await showCard(`<div class="k">What Jejak Asap is</div><h1>Know when the haze arrives.</h1>
    <p>Fires + wind give each city a chance of smoke and an arrival hour. Measured air keeps it honest.</p>
    <p>SAM 3.1 and Muse Spark show where the smoke is and turn the forecast into clear, checked messages for residents, schools and health centres.</p>
    <p style="color:#9aaaa2;font-size:22px">M. Sopian Hadianto · github.com/mshadianto/jejak-asap · NASA FIRMS, NASA GIBS, ERA5, CAMS · Meta SAM 3.1 + Muse Spark 1.3</p>`);
  await say(176, "Fires and wind tell each city when the haze arrives. SAM 3.1 and Muse Spark show where the smoke is, and turn the forecast into clear, checked messages for the people who need them.");
  await at(192.5); await page.evaluate(() => { const c = document.getElementById('cap'); if (c) c.remove(); });
  await at(196);
  await ctx.close();
  await browser.close();
})();
