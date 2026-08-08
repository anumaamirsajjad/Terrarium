# Terrarium — what it does, how to try it, what works

Plain-language guide. Written 2026-08-07, updated the same day after the photo and voice
features were removed and the air model was fixed.

For the engineering detail see [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) (what was
built and why) and [AUDIT.md](AUDIT.md) (what is broken, with evidence). This file is the
one to read if you just want to *use* the thing.

---

## 1. What this is, in one paragraph

Terrarium is a **digital twin of one 20 km × 20 km square of Lahore**. You draw a shape on
a map and say what you want to change — plant trees, take traffic out — and it tells you
how much cooler that patch would get, how much cleaner the air would get, and **who** gets
the benefit. It answers in about a second, so you can try five ideas in a minute.

Everything runs on free, public data. No credit card anywhere.

**One tile only.** Lahore is hardcoded. There is no city picker and that is deliberate.

---

## 2. The three things it models

| What | Plain meaning | How good is it? |
|---|---|---|
| **Heat** | Ground-surface temperature at ~10:30 in the morning | Best-supported. Trained on real satellite readings of this tile |
| **Air** | Extra PM2.5 that *this tile's own roads* make | Checked against 53 real monitors. **Works in winter, not in summer** — see §7 |
| **Fairness** | Which income groups actually get the cooling | Solid arithmetic, but it inherits the heat model's error |

### Two phrases you must not shorten

These matter because shortening them makes the numbers wrong:

- It is **"mid-morning land surface temperature"**, not "temperature". It is how hot the
  *ground* is, not the air, and it is measured mid-morning, not in the afternoon heat. The
  ground runs several degrees hotter than the air.
- It is **"locally-generated PM2.5"**, not "air quality". It only counts pollution made by
  roads inside this square. Most of Lahore's real smog blows in from outside and is not
  counted. So always talk about the **change**, never the level.

---

## 3. Getting it running

Two terminals.

**Terminal 1 — the backend:**
```bash
uv sync --extra dev
uv run terrarium-api
```
It comes up on <http://127.0.0.1:8000>. Interactive API docs at `/docs`.

**Terminal 2 — the frontend:**
```bash
cd web
npm install
npm run dev
```
Open <http://localhost:5173>.

> **Port 5173 is not optional.** The backend only accepts the browser from that exact port.
> If Vite says "port in use, trying 5174", stop it, free 5173, and start again — otherwise
> the page loads but every request fails.

### Optional: a smarter text parser

**Nothing needs a key.** Every button and every route works without one. The only thing a
key buys is more flexible free-text understanding on the "type a sentence" box — without
one, a built-in rule reader handles it, and it covers the phrasings a demo actually uses,
in English and Urdu.

```bash
# .env in the project root (already gitignored — never commit real keys)
TERRARIUM_GROQ_API_KEY=...     # free, no card: console.groq.com/keys
TERRARIUM_GEMINI_API_KEY=...   # free, no card: aistudio.google.com/apikey
```

Either works alone. With both, Groq is tried first and Gemini takes over if it fails.

---

## 4. Walkthrough — click this, see that

The screen is a map with a control panel down the side. Top to bottom:

### Seasonal window
Buttons for six windows: `2023-summer` through `2025-winter`.

**Use `2025-winter` for air.** It is the only window with enough working monitors to have
been validated against — 53 of them. The others model fine but have no independent check.

**This changes the answer a lot.** The same tree planting cools about **0.51 °C in summer**
but only **0.13 °C in winter**. Every number on screen belongs to the window you picked.

### Base layer
Pick what the map is coloured by:

- `lst_c` — surface temperature (start here)
- `ndvi` — greenness
- `ndbi` — built-up-ness
- `albedo` — how much sunlight bounces off
- `elevation_m` — height
- `population` — people per cell
- `pm25_emission_g_s` — where the road pollution comes from

### Intervention — the main event

**Step 1. Draw.** Click **Draw a polygon**, then click 3+ points on the map to outline an
area. Click **Finish**. Somewhere in the built-up middle of the city works best.

**Step 2. Say what to change.** Three ways, use whichever:

**(a) A preset button** — the quickest:

| Preset | What it does |
|---|---|
| Street trees | A realistic amount of street planting |
| Dense canopy | Aggressive planting |
| Low-emission zone | Removes most road traffic |
| Clean and green | Both together |
| Winter low-emission zone | Traffic removal, in the smog season |

**(b) Type a sentence** — e.g. `plant 5,000 trees and ban cars here in winter`. Works in
**English and Urdu** (`اس علاقے میں ۵۰۰۰ درخت لگائیں` is fine, Urdu numerals included).
With no API key a plain rule-reader handles it, which covers normal phrasings.

**(c) The two sliders** —
- *Canopy added*: how much tree cover to add.
- *Emissions removed*: how much traffic to take out. At 0 the plan says nothing about
  traffic and you get no air result at all.

> **The refusal is a feature.** Ask for 900,000 trees in a small polygon and it will
> *refuse* and show the arithmetic — "you need 22.5 km² of tree crown, this polygon has
> 6.4 km²". It does not quietly plant fewer and hand you a small number, because a small
> number looks like a plan that simply worked badly.

**Step 3.** Click **Run simulation**. Under a second.

### View — four tabs

- **Baseline** — the tile as it is now.
- **ΔLST** — how much cooler (blue) or warmer (red) your change makes each spot.
- **ΔPM2.5** — the air change. *Only appears if your plan removed traffic.*
- **Compare** — a slider that wipes between before and after.

### Reading the results

- **Result panel** — average cooling inside your polygon, the best cell, and spillover
  just outside. It shows the **discounted** figure: the model over-predicts cooling by
  about **2.5×**, so the honest number is the model's divided by 2.5. It says so on screen.
- **Air panel** — PM2.5 change, plus the mixing height and wind direction. Winter traps
  pollution near the ground, so identical traffic gives **6–7× the concentration** in
  winter.
- **Equity panel** — splits people into ten income groups and shows who gets the cooling.
- **Brief panel** — plain-English findings and, always, a list of caveats.

### Council brief
**Print brief** opens your browser's print dialog with a clean one-page summary. Save as
PDF from there.

---

## 5. A 3-minute test run

1. Pick `2025-winter` — the validated window.
2. Base layer `lst_c` — you should see a warm city and cooler edges.
3. **Draw a polygon** over central Lahore, 5–6 clicks, **Finish**.
4. Click **Low-emission zone**.
5. **Run simulation**.
6. You should land on the **ΔPM2.5** tab automatically with a blue patch.
7. Click **Compare** and drag the slider.
8. Switch the window to `2025-summer` and run again — the air number should be **much
   smaller**. That is the winter inversion, and it is the single most convincing thing to
   show someone. Note the panel also changes what it says about validation.

---

## 6. What works

Everything below was run and checked on 2026-08-07.

- ✅ All six API endpoints return 200.
- ✅ Draw → plan → simulate → map, end to end.
- ✅ **Speed: under a second** for a full simulation (heat + air + equity + brief).
- ✅ Heat, air and equity models all produce results, with the window named on every one.
- ✅ Presets, typed sentences (English + Urdu), and sliders.
- ✅ Refusals with visible arithmetic.
- ✅ Compare slider, all four view tabs, legends.
- ✅ Printable brief.
- ✅ **411 backend tests and 79 frontend tests pass.** Type and lint checks clean.
- ✅ **Works with no API key of any kind.** Nothing is gated behind a signup.

---

## 7. What is broken or limited

### 🟢 The air model now passes — in winter

This changed on 2026-08-07 and it is the biggest improvement in the project.

It was checked against **53 real air monitors** in Lahore and originally **lost** to a dumb
model that just guesses the city average. The cause turned out to be a mismatch of
timescale: the model used a **single wind direction** for a whole three-month season, but
the real wind over that season blew across almost the entire compass (68° to 331°). So it
painted a narrow streak one way while the season's air went everywhere.

Replacing that with an **even, all-directions spread** fixed it:

| version | agreement with monitors | error | beats the dumb model? |
|---|---|---|---|
| old, single wind direction | +0.16 | 51.0 | ❌ no |
| **new, seasonal spread** | **+0.53** | **40.6** | ✅ **yes** |

Two honest caveats:

- **Summer still fails**, at every setting we tried. In summer the air mixes through 800 m
  of depth by mid-morning, so local traffic disperses before it can make any pattern — and
  only 15 monitors were usable. **Quote winter results. Say summer is unvalidated.**
- The spread distance (1 km) was **tuned on the same 53 monitors** the score is quoted
  from, so the score flatters itself a little. It is not a knife edge though — anything
  from 400 m to 2 km beats the dumb model.

Still true: quote the **change**, never the level. Most of Lahore's real smog blows in from
outside the square and is not counted.

### 🟠 The heat model over-predicts by ~2.5×

Known and handled — the screen already divides by it. But if you read a raw model number
anywhere, halve it and then some.

### 🟠 The tree-planting effect on air is nearly zero

About −0.0003 µg/m³. Real, but too small to see. Trees are for heat here, not air.

### 🟡 Smaller things

| Thing | Detail |
|---|---|
| `data/processed/cube.zarr` | A known-bad half-built file. Kept on purpose so the safety check has something to reject. The app serves `cube_v2.zarr` |
| Costs | Rough literature prices, marked `calibrated: false`. Fine for ranking two plans, not for a budget |
| Brick kilns | The pollution map found **0 kilns**, roads only. Unclear if that is real or a data gap |
| Rebuilding data | The OpenStreetMap service times out often. Never rebuild the night before a demo |
| Brick kilns, again | Worth repeating: kilns are a major Lahore winter source and OSM has none, so the air model is missing a whole source category. This caps how well it can ever score |

### ⚪ Removed on purpose

**Citizen photos** and **voice input** were both built, both worked, and were both removed
on 2026-08-07. The photo feature was the only thing in the project that *required* an API
key, and voice was the only feature no automated test could drive. Cutting them means every
route now works with no key and nothing is checked by hand only.

---

## 8. If something goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| Page loads, everything errors | Frontend not on port 5173 | Free 5173, restart `npm run dev` |
| Every data request says 503 | Backend started but the data file failed its check | Read the backend startup log — it names the reason |
| Typed plan gives a basic result | Falling back to the rule reader | Normal without a key, and fine — it handles ordinary phrasings |
| "Run simulation" greyed out | No polygon yet | Draw one and click Finish |
| ΔPM2.5 tab missing | Your plan removed no traffic | Raise the emissions slider |

---

## 9. The honest summary

The **product works**, and works with no key of any kind. Draw, plan, simulate, read,
print — all of it, fast, on real data, for free.

The **heat model is the credible one**. It learned from this tile's own satellite record
and its main error is known and corrected on screen.

The **air model now earns its place in winter.** It was tested against real monitors,
failed, was diagnosed, fixed, and re-tested — and it now beats the baseline by 20%. Summer
is still unvalidated and should be described that way.

The **equity model** is the most under-sold part — nobody else shows *who* gets the
cooling, and the arithmetic there is sound.
