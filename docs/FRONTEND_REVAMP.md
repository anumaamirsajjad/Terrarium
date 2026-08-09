# Frontend revamp — implementation guide

"Solarpunk Data Science": a cinematic landing page and a Linear-style floating dashboard,
built on the frontend that exists today rather than the one the brief assumes.

Scope: `web/` only. `src/terrarium/` and `web/src/api/client.ts` are not touched, and
neither is `web/src/raster/decode.ts`. Everything below is presentation.

---

## 0. Read this first — four places the brief and the repo disagree

Fix these before writing code, because three of them are copy that would ship a false
claim and one is a missing dependency that half the brief assumes.

### 0.1 There is no Tailwind, and no router

`web/package.json` has five runtime dependencies: `deck.gl`, `@deck.gl/react`,
`maplibre-gl`, `react`, `react-dom`. Styling today is 696 hand-written lines in
[App.css](../web/src/App.css) driven by CSS custom properties in
[index.css](../web/src/index.css). The brief says "use inline Tailwind classes
extensively" — so Tailwind is an install step, not a given, and it has to co-exist with
`App.css` during the transition rather than replace it in one commit (§6 explains why the
print stylesheet in particular must survive intact).

There is also no router. §3 covers that; it does not need `react-router`.

### 0.2 Voice was removed on 2026-08-07 — do not put it back in the copy

The brief asks for "Web Speech API integration" on the landing page and a "native voice
toggle" in the command palette. Voice capture and citizen photos were both **cut**
(CLAUDE.md, D19/D20 closed as withdrawn), and the reason is load-bearing: they were the
only two features that could not be defended offline, and removing them is what makes
*"every route works with no key at all"* unconditional.

Advertising a microphone that does not exist breaks the brief's own rule — *do not invent
features*. The honest version of that section is stronger anyway, and all of it is real:

- **English and Urdu**, both parsed by the same deterministic rule parser.
- **Eastern Arabic-Indic digit folding** — ۵۰۰۰ matches no `\d`, so without the folding
  pass an Urdu sentence parses as a plan with no quantity in it.
- **LLM-free by default.** `TERRARIUM_GEMINI_API_KEY` unset and `/plan` still answers;
  the panel prints which parser ran, from `PresetsResponse.planner`, which reads
  `"rules (no model configured)"` on a keyless deployment.

So: keep the typing animation, keep the Urdu retype, drop the microphone. §4.4.

### 0.3 There is no `ObservationsPanel`

The brief lists it among the panels to wrap in glass. The citizen observation layer went
with the photo route in the same removal. `web/src/panels/` contains exactly:
`AirPanel`, `BriefDocument`, `BriefPanel`, `EquityPanel`, `Legend`, `ResultPanel`,
`ScenarioPanel`. Nothing else needs wrapping.

### 0.4 The winter figure is 6.3×–8.9×, not 7.4×

7.4× is a stale single-year number (2024). [AUDIT.md:63](AUDIT.md#L63) closed that as a
docs defect on 2026-08-07; the measured range across 2023, 2024 and 2025 is **6.3×–8.9×**,
and `cores/air.py:212` is the source of truth. Landing copy should say **6–9×**.

While in the neighbourhood, these three are correct as the brief states them and should be
quoted verbatim: the **2.5×** hindcast over-prediction (−1.18 °C modelled vs −0.47 °C
observed, over-predicting in 12 of 12 test configurations), **100 m** resolution
(201 × 202 = 40,602 cells), and **EPSG:32643**.

---

## 1. Dependencies

```bash
cd web

# UI + motion + icons
npm install motion lucide-react

# 3D hero
npm install three @react-three/fiber @react-three/drei
npm install -D @types/three

# Styling
npm install tailwindcss @tailwindcss/vite

# Self-hosted Geist — keyless, no CDN at demo time (zero-budget rule)
npm install @fontsource-variable/geist @fontsource-variable/geist-mono
```

Four notes on those choices:

- **`motion`, not `framer-motion`.** Framer Motion became the independent `motion`
  project; the API is identical, the import path is `motion/react`. Installing
  `framer-motion` today gets you a package pointing at the old home.
- **`@react-three/fiber` v9 is the React 19 line.** v8 pairs with React 18 and will not
  mount. `@react-three/drei` ≥ 10.x is the matching major.
- **No new deck.gl package.** `PathStyleExtension`, `PostProcessEffect` and the layer
  classes all ship inside the `deck.gl` umbrella already installed — `@deck.gl/mapbox` is
  imported that way in [MapView.tsx:12](../web/src/map/MapView.tsx#L12) today.
- **Fonts self-hosted, not Google Fonts.** Google Fonts is free and keyless, so it does
  not break the budget rule, but it is a network dependency at demo time and D13's whole
  point is that a live pitch should not have one. Fontsource ships the woff2 into the
  bundle.

`vite.config.ts` — add the plugin, keep the worker block exactly as it is (deleting
`worker: { format: 'es' }` blanks the basemap in a near-silent way):

```ts
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  worker: { format: 'es' },
})
```

---

## 2. Design tokens

Tailwind v4 has no `tailwind.config.js`. The theme is CSS. Replace
[web/src/index.css](../web/src/index.css) with the block below — keeping the existing
custom-property names as aliases, because `App.css` reads `--bg`, `--surface`, `--border`,
`--fg`, `--fg-muted`, `--badge-bg`, `--ok`, `--error` in ~90 places and the print
stylesheet depends on some of them.

```css
@import "tailwindcss";
@import "@fontsource-variable/geist";
@import "@fontsource-variable/geist-mono";

@theme {
  --font-sans: "Geist Variable", system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-mono: "Geist Mono Variable", ui-monospace, monospace;

  /* Substrate */
  --color-void:  #05070a;   /* page black */
  --color-shell: #0a0d12;   /* panel base, before blur */
  --color-hair:  rgba(255, 255, 255, 0.10);   /* the 1px border, everywhere */

  /* Data accents — see the warning below before using these on a raster */
  --color-cool:  #22d3ee;   /* cyan   — cooling, ΔLST negative */
  --color-heat:  #fb923c;   /* orange — warming, LST high */
  --color-haze:  #a855f7;   /* purple — PM2.5 */
  --color-life:  #4ade80;   /* green  — canopy, ok state */

  --blur-glass: 16px;
}

/* Dark by default; the old light-scheme block is dropped. A frosted-glass shell over a
   pale Positron basemap does not survive a light theme, and the map is the product. */
:root {
  color-scheme: dark;

  --bg: var(--color-void);
  --surface: var(--color-shell);
  --border: var(--color-hair);
  --fg: #e8ece9;
  --fg-muted: #8b948f;
  --badge-bg: rgba(255, 255, 255, 0.04);
  --ok: var(--color-life);
  --error: #f87171;

  font-family: var(--font-sans);
  line-height: 1.6;
}

body {
  margin: 0;
  min-height: 100vh;
  background: var(--bg);
  color: var(--fg);
  -webkit-font-smoothing: antialiased;
}

/* One utility, used by every floating panel. Cheaper than repeating six classes. */
@utility glass {
  background: color-mix(in oklab, var(--color-shell) 72%, transparent);
  backdrop-filter: blur(var(--blur-glass)) saturate(140%);
  border: 1px solid var(--color-hair);
  border-radius: 0.875rem;
}

/* Noise overlay. An inline feTurbulence data URI — no image asset to download, no
   licence to check, ~200 bytes. */
@utility noise {
  position: relative;
  &::after {
    content: "";
    position: absolute;
    inset: 0;
    pointer-events: none;
    border-radius: inherit;
    opacity: 0.035;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='3'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
  }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
```

> **The accent palette is for chrome, not for data.** The map's colours are fixed in
> [raster/ramp.ts](../web/src/raster/ramp.ts) and they encode meaning: `DIVERGING` is
> blue→transparent→red with **zero fully transparent**, which is the single most important
> property on that screen (~39,000 of 40,602 cells are *exactly* 0.000 after a simulation,
> and colouring them would bury the intervention). `--color-haze` may tint the **AirPanel's
> chrome**; it must not become a PM2.5 ramp, because ΔPM2.5 renders through the same
> `DIVERGING` ramp as ΔLST ([App.tsx:265](../web/src/App.tsx#L265)) and a purple legend
> over a red raster is a lie. Same for cyan/orange: the legend swatch is generated from the
> ramp's own stops in [Legend.tsx:23](../web/src/panels/Legend.tsx#L23) and stays that way.

Spring vocabulary — define once, import everywhere, so the whole app feels like one object:

```ts
// web/src/motion/springs.ts
import type { Transition } from "motion/react";

export const SNAP: Transition   = { type: "spring", stiffness: 520, damping: 34 };  // buttons, toggles
export const GLIDE: Transition  = { type: "spring", stiffness: 210, damping: 26 };  // panels in/out
export const SETTLE: Transition = { type: "spring", stiffness: 90,  damping: 20 };  // bars, big reveals

export const RISE = {
  hidden: { opacity: 0, y: 24 },
  show:   { opacity: 1, y: 0, transition: SETTLE },
};

export const STAGGER = {
  hidden: {},
  show: { transition: { staggerChildren: 0.07 } },
};
```

---

## 3. Routing: two entry points, one bundle split

`three` + `drei` is roughly 700 kB of JS that the dashboard never needs, and the dashboard
opens MapLibre and deck.gl that the landing page never needs. Splitting them matters more
than the routing mechanism does.

No router library. A hash check plus `React.lazy` is nine lines and gives the split for
free:

```tsx
// web/src/main.tsx
import { StrictMode, Suspense, lazy, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";

const Landing = lazy(() => import("./pages/Landing"));
const App = lazy(() => import("./App"));

function Root() {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const onHash = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return (
    <Suspense fallback={<div className="grid h-screen place-items-center text-white/40">…</div>}>
      {hash === "#/app" ? <App /> : <Landing />}
    </Suspense>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
```

`href="#/app"` is the CTA. Add `react-router` when there is a third route to justify it.

---

## 4. The landing page

```
web/src/pages/
├── Landing.tsx            composition root: sections in order, nothing else
└── landing/
    ├── Hero.tsx           headline, magnetic CTA
    ├── TerrainCanvas.tsx  R3F wireframe grid + mouse spotlight
    ├── Bento.tsx          three philosophy cards
    ├── Simulators.tsx     sticky-scroll narrative, three panes
    ├── ThermalViz.tsx     2D canvas heat map that cools
    ├── PlumeViz.tsx       2D canvas particle flow that bottlenecks
    ├── EquityViz.tsx      bar chart, empty-land bar glowing
    ├── CommandDemo.tsx    typing → Urdu retype
    └── Outro.tsx          council brief + footer
```

### 4.1 Hero

**The grid.** A single `THREE.PlaneGeometry` rendered as wireframe, displaced in the vertex
shader, with a mouse-tracked spotlight applied in the fragment shader. One shader,
one draw call, no post-processing, no `drei` helper needed for the mesh itself.

```tsx
// web/src/pages/landing/TerrainCanvas.tsx
import { Canvas, useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";

const vertex = /* glsl */ `
  uniform float uTime;
  varying vec3  vPos;

  // Value noise — cheap, and the terrain only needs to read as a city, not be one.
  float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
  float noise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x),
               mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
  }

  void main() {
    vec3 p = position;
    // Two octaves: a broad basin plus block-scale relief, quantised so it reads as
    // buildings rather than as hills.
    float h = noise(p.xy * 0.18 + uTime * 0.02) * 1.6
            + floor(noise(p.xy * 0.9) * 5.0) / 5.0 * 0.9;
    p.z = h;
    vPos = p;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
  }
`;

const fragment = /* glsl */ `
  uniform vec2  uMouse;   // world-space, on the plane
  uniform vec3  uBase;
  uniform vec3  uGlow;
  varying vec3  vPos;

  void main() {
    float d    = distance(vPos.xy, uMouse);
    float lamp = smoothstep(9.0, 0.0, d);          // the spotlight
    float fade = smoothstep(26.0, 6.0, length(vPos.xy));  // horizon falloff
    vec3  col  = mix(uBase, uGlow, lamp);
    gl_FragColor = vec4(col, (0.16 + lamp * 0.75) * fade);
  }
`;

function Terrain() {
  const material = useRef<THREE.ShaderMaterial>(null);
  const uniforms = useMemo(
    () => ({
      uTime:  { value: 0 },
      uMouse: { value: new THREE.Vector2(0, 0) },
      uBase:  { value: new THREE.Color("#0e3a4a") },
      uGlow:  { value: new THREE.Color("#22d3ee") },
    }),
    [],
  );

  useFrame(({ clock, pointer, viewport }) => {
    if (!material.current) return;
    uniforms.uTime.value = clock.elapsedTime;
    // Ease toward the pointer rather than snapping: the lamp should feel heavy.
    uniforms.uMouse.value.lerp(
      new THREE.Vector2(pointer.x * viewport.width * 0.9, pointer.y * viewport.height * 0.9),
      0.06,
    );
  });

  return (
    <mesh rotation={[-Math.PI / 2.6, 0, 0]}>
      <planeGeometry args={[70, 70, 130, 130]} />
      <shaderMaterial
        ref={material}
        uniforms={uniforms}
        vertexShader={vertex}
        fragmentShader={fragment}
        wireframe
        transparent
        depthWrite={false}
      />
    </mesh>
  );
}

export default function TerrainCanvas() {
  return (
    <Canvas
      className="absolute inset-0"
      camera={{ position: [0, 9, 20], fov: 42 }}
      dpr={[1, 1.75]}                       // uncapped DPR melts a 4K laptop for no gain
      gl={{ antialias: true, alpha: true }}
      frameloop="demand"                    // see the note below
    >
      <Terrain />
    </Canvas>
  );
}
```

Two things about that canvas:

- `frameloop="demand"` plus `useFrame` will *not* animate. Pick one: keep the continuous
  loop (drop `frameloop`) for the slow rotation the brief asks for, or keep `demand` and
  invalidate on pointer move only. Continuous at `dpr` ≤ 1.75 on a 130×130 grid is ~1 ms a
  frame; that is the recommendation, so **delete the `frameloop` prop**. It is written above
  only so the trap is visible.
- Gate the whole canvas behind `matchMedia("(prefers-reduced-motion: reduce)")` and render
  a static CSS gradient instead. The CSS block in §2 kills transitions but cannot stop a
  WebGL render loop.

**Optional upgrade — make it the real Lahore.** The hero grid can be actual elevation
rather than noise. `/cube/layer/elevation_m` returns the tile as base64 float32; dump it
once to a 201×202 greyscale PNG in `web/public/`, load it with `useTexture`, and sample it
in the vertex shader instead of `noise()`. Do this only after the rest ships — and if you
do, label it, because `cop-dem-glo-30` is a Digital *Surface* Model and over dense Lahore
it reads above bare ground.

**Magnetic CTA.** `useSpring` on a translate, driven by pointer offset from the button's
centre, released on leave:

```tsx
// web/src/pages/landing/Hero.tsx (excerpt)
import { motion, useMotionValue, useSpring } from "motion/react";
import { useRef } from "react";
import { ArrowRight } from "lucide-react";
import { SNAP } from "../../motion/springs";

function MagneticCTA() {
  const ref = useRef<HTMLAnchorElement>(null);
  const x = useSpring(useMotionValue(0), SNAP);
  const y = useSpring(useMotionValue(0), SNAP);

  return (
    <motion.a
      ref={ref}
      href="#/app"
      style={{ x, y }}
      onPointerMove={(e) => {
        const b = ref.current!.getBoundingClientRect();
        // 0.35 keeps the pull suggestive. At 1.0 the button chases the cursor and
        // becomes hard to actually click.
        x.set((e.clientX - (b.left + b.width / 2)) * 0.35);
        y.set((e.clientY - (b.top + b.height / 2)) * 0.35);
      }}
      onPointerLeave={() => { x.set(0); y.set(0); }}
      whileTap={{ scale: 0.97 }}
      className="group relative inline-flex items-center gap-2 overflow-hidden rounded-full
                 border border-white/15 bg-white/5 px-8 py-4 text-sm font-medium
                 tracking-tight backdrop-blur-xl"
    >
      <span
        aria-hidden
        className="absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent
                   via-cyan-400/25 to-transparent transition-transform duration-700
                   group-hover:translate-x-full"
      />
      <span className="relative">Enter the Sandbox</span>
      <ArrowRight className="relative size-4 transition-transform group-hover:translate-x-1" />
    </motion.a>
  );
}
```

Headline: `text-[clamp(3.5rem,13vw,11rem)] font-medium tracking-[-0.055em] leading-[0.85]`.
Subheadline stays exact: *"A Neighbourhood-Scale Digital Twin for Climate Intervention."*

### 4.2 Bento grid

`grid-cols-6` with spans, so the asymmetry is structural rather than three equal cards
pretending:

```tsx
<motion.section
  variants={STAGGER} initial="hidden" whileInView="show"
  viewport={{ once: true, amount: 0.25 }}
  className="mx-auto grid max-w-6xl grid-cols-1 gap-3 px-6 py-32 md:grid-cols-6"
>
  <motion.article variants={RISE} className="glass noise p-8 md:col-span-4 md:row-span-2">
    <Wallet className="size-5 text-cyan-300" />
    <h3 className="mt-6 text-3xl tracking-tight">Zero cost. Zero keys.</h3>
    <p className="mt-3 max-w-md text-white/55">
      Landsat, Sentinel-2, Copernicus DEM and ESA WorldCover through Microsoft Planetary
      Computer with anonymous signing; ERA5 and WorldPop over plain HTTP; roads from
      Overpass; basemap tiles from OpenFreeMap. Nothing here asks for a credit card, and
      every route answers with no API key set at all.
    </p>
  </motion.article>

  <motion.article variants={RISE} className="glass noise p-8 md:col-span-2">
    <TerminalSquare className="size-5 text-emerald-300" />
    <h3 className="mt-6 text-xl tracking-tight">Local fallback</h3>
    <p className="mt-3 text-sm text-white/55">
      A language model is a front door, never the arbiter. With no key the planner is a
      deterministic rule parser, and whatever produces a plan it is re-validated against
      the tile before a simulator sees a number.
    </p>
  </motion.article>

  <motion.article variants={RISE} className="glass noise p-8 md:col-span-2">
    <Globe2 className="size-5 text-orange-300" />
    <h3 className="mt-6 text-xl tracking-tight">Any city on Earth</h3>
    <p className="mt-3 text-sm text-white/55">Starting in Lahore.</p>
    <dl className="mt-6 space-y-1 font-mono text-xs text-white/40">
      <div className="flex justify-between"><dt>CRS</dt><dd>EPSG:32643</dd></div>
      <div className="flex justify-between"><dt>Grid</dt><dd>201 × 202</dd></div>
      <div className="flex justify-between"><dt>Cell</dt><dd>100 m</dd></div>
    </dl>
  </motion.article>
</motion.section>
```

`viewport={{ once: true }}` is not optional — cards that re-animate on every scroll-past
are the tell of a template.

### 4.3 Sticky-scroll simulators

One `useScroll` over a 300vh section; the left column is `sticky top-0`, the right swaps
visualisation by scroll progress. Copy is exact:

| Pane | Copy (verbatim) |
|---|---|
| Thermal | "Predicts mid-morning land surface temperature. 100 m resolution. Built-in hindcast validation exposes our own 2.5× space-for-time over-prediction." |
| Air | "Simulates locally-generated PM2.5 via OSM and seasonal FFT dispersion. Winter inversion captures **6–9×** the concentration of identical summer sources." |
| Equity | "Measures person-degrees, not just pixels. Because a plan that cools an empty riverbank fails the city." |

```tsx
// web/src/pages/landing/Simulators.tsx
import { motion, useScroll, useTransform, useMotionValueEvent } from "motion/react";
import { useRef, useState } from "react";

const PANES = [
  { key: "thermal", title: "Thermal Core",    body: "…", Viz: ThermalViz },
  { key: "air",     title: "Air Dispersion",  body: "…", Viz: PlumeViz },
  { key: "equity",  title: "Equity Core",     body: "…", Viz: EquityViz },
] as const;

export default function Simulators() {
  const track = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({ target: track, offset: ["start start", "end end"] });
  const [active, setActive] = useState(0);

  useMotionValueEvent(scrollYProgress, "change", (p) => {
    setActive(Math.min(PANES.length - 1, Math.floor(p * PANES.length)));
  });

  return (
    <div ref={track} className="relative h-[300vh]">
      <div className="sticky top-0 grid h-screen items-center gap-12 px-8 lg:grid-cols-2">
        <div className="relative">
          {PANES.map((pane, i) => (
            <motion.div
              key={pane.key}
              animate={{ opacity: active === i ? 1 : 0, y: active === i ? 0 : 20 }}
              transition={GLIDE}
              className={active === i ? "" : "pointer-events-none absolute inset-0"}
            >
              <span className="font-mono text-xs text-cyan-300/70">0{i + 1} / 03</span>
              <h2 className="mt-3 text-5xl tracking-tight">{pane.title}</h2>
              <p className="mt-5 max-w-md text-lg leading-relaxed text-white/55">{pane.body}</p>
            </motion.div>
          ))}
        </div>
        <div className="glass noise aspect-square w-full overflow-hidden">
          {PANES.map(({ key, Viz }, i) => (
            <Viz key={key} active={active === i} progress={scrollYProgress} />
          ))}
        </div>
      </div>
    </div>
  );
}
```

The three visualisations are plain `<canvas>` with a `requestAnimationFrame` loop, not R3F
— they are 2D, and mounting a second WebGL context for a heat grid is how a landing page
starts dropping frames.

- **ThermalViz** — a 24×24 grid, each cell warm-coloured from a noise field; on `active`,
  interpolate a canopy mask in over ~1.2 s and subtract from the field. Use `HEAT` from
  `raster/ramp.ts` so the landing page and the map share one temperature palette. Cap the
  cooling shown at something under the real ceiling — `tree_built_contrast_c` runs ~2.6 °C
  in summer on this tile, and an animation implying 8 °C contradicts the panel the user
  reaches thirty seconds later.
- **PlumeViz** — ~400 particles advecting left-to-right; on `active`, drop the vertical
  extent and the speed to a third and watch density pile up. That is the physical claim
  (mixing height ~250 m in winter against ~800 m in summer, lighter winds), and it reads
  correctly as a bottleneck.
- **EquityViz** — ten bars, animating from zero with `SETTLE` and a per-bar delay. Bar 1
  ("reaches nobody") gets `--color-error` and a slow opacity pulse.

### 4.4 The command prompt

CMD+K mockup, typing loop, no microphone (§0.2).

```tsx
const LINES = [
  { text: "Plant 5,000 trees near the Canal", dir: "ltr" as const },
  { text: "سردیوں میں گاڑیوں پر پابندی",       dir: "rtl" as const },
];
```

Cycle: type at ~45 ms/char → hold 2 s → delete at ~25 ms/char → next. Store the whole
string and slice it by an index in state; **do not** animate per-character DOM nodes,
because Urdu is a cursive script and splitting it into separate elements breaks the joins
and renders as disconnected letterforms. One text node, `dir={line.dir}`, sliced.

Caret: `<motion.span animate={{opacity:[1,1,0,0]}} transition={{duration:1, repeat:Infinity, times:[0,0.5,0.5,1]}}>`.

Copy underneath, all of it true:

> Typed in English or Urdu. Eastern Arabic-Indic digits are folded to ASCII before any
> pattern runs — ۵۰۰۰ matches no `\d`, and without that pass an Urdu sentence parses as a
> plan with no quantity in it. A language model is optional and lives in exactly one file;
> with no key set, the same sentence is parsed by a deterministic rule parser and the
> panel says so. Either way the plan is re-validated against the polygon before a
> simulator runs, and an impossible plan is **refused** with the arithmetic attached:
> *"5,000 trees need 0.125 km² of crown at 25 m² each, but this polygon has only
> 0.031 km² still plantable."*

### 4.5 Outro and footer

Council brief section: a mocked A4 sheet, `rotate-[-2deg] hover:rotate-0`, listing what the
real one carries — headline, findings, uncertainties (never empty), confidence (never
"high"), the hindcast correction, the window, and the surface-vs-air distinction. Say it is
the browser's own print dialog: no rendering service, no font stack, no extra dependency.

Footer: `Dashboard` (`#/app`), `GitHub`, `Documentation`. Close with the D9 line, which has
to appear wherever the temperature does:

> `lst_c` and `ΔLST` are mid-morning land surface temperature (~10:30 local, Landsat
> ST_B10) — the radiating surface, not air temperature, and not the afternoon peak.

---

## 5. The dashboard

### 5.1 Extruded intervention zone

[MapView.tsx:141](../web/src/map/MapView.tsx#L141) already builds the `PolygonLayer`. It
needs four props and a light, because an extruded polygon with no `LightingEffect` renders
as a flat silhouette:

```ts
new PolygonLayer({
  id: "drawn-polygon",
  data: [{ polygon: [...vertices, vertices[0]!] }],
  getPolygon: (d: { polygon: Position[] }) => d.polygon,
  getFillColor: polygonClosed ? DRAW_FILL : DRAW_FILL_OPEN,
  getLineColor: DRAW_LINE,
  getLineWidth: 2,
  lineWidthUnits: "pixels",
  filled: true,
  stroked: true,
  // Only once closed. Extruding an open ring gives a wall across an area the user has
  // not finished defining.
  extruded: polygonClosed,
  getElevation: 220,           // metres. ~2 px at the tile's default zoom — visible, not a tower.
  wireframe: true,
  material: { ambient: 0.5, diffuse: 0.6, shininess: 32, specularColor: [80, 200, 230] },
  pickable: false,
})
```

Add to the overlay construction:

```ts
import { AmbientLight, LightingEffect, _SunLight as SunLight } from "@deck.gl/core";

const lighting = new LightingEffect({
  ambient: new AmbientLight({ color: [255, 255, 255], intensity: 1.4 }),
  sun: new SunLight({ timestamp: Date.UTC(2024, 5, 15, 5, 30), color: [255, 245, 230], intensity: 1.1 }),
});
const overlayInstance = new MapboxOverlay({ interleaved: false, layers: [], effects: [lighting] });
```

`Date.UTC(2024, 5, 15, 5, 30)` is 10:30 Lahore time (UTC+5). The shadows fall where the
overpass put them — a free half-second of consistency.

The map must also tilt for extrusion to read at all. In the `MapLibreMap` constructor add
`pitch: 45` — or better, leave it flat and pitch only once a polygon closes:

```ts
useEffect(() => {
  if (polygonClosed) map.current?.easeTo({ pitch: 45, duration: 900 });
}, [polygonClosed]);
```

### 5.2 Bloom

**deck.gl cannot do this natively.** `PostProcessEffect` accepts luma.gl shader modules,
and luma.gl v9's image-processing set has no bloom or glow pass — it ships
`brightnessContrast`, `triangleBlur`, `zoomBlur`, `hueSaturation`, `vibrance`, `vignette`,
`tiltShift`, `ink`, `dotScreen`, `edgeWork` and friends. `PostProcessEffect(triangleBlur)`
would blur the raster, which is the opposite of the requirement: nearest-neighbour
sampling is deliberate in [MapView.tsx:125](../web/src/map/MapView.tsx#L125) because at
100 m a cell is a measured unit and smoothing invents gradients the cube does not contain.

Cheapest thing that actually works: composite the glow into the overlay canvas before
deck.gl ever sees it. `toCanvas` in [raster/canvas.ts](../web/src/raster/canvas.ts)
already turns colourised RGBA into an `HTMLCanvasElement`; wrap it. This touches neither
the decoder nor the ramp — the pixel values are unchanged, a blurred copy is composited
*under* the crisp one.

```ts
// web/src/raster/glow.ts
import type { Raster } from "./decode";

/**
 * A blurred, additively-composited copy beneath the crisp raster.
 *
 * The top layer is drawn last and unfiltered, so the nearest-neighbour edges the map
 * depends on are exactly preserved; only the halo around them is soft.
 *
 * ponytail: the blur radius is in *cell* space, so the halo scales with zoom instead of
 * staying a fixed number of screen pixels. Fine for a 20 km tile at one zoom range. If it
 * ever needs to be zoom-invariant, that is a real deck.gl PostProcessEffect with a custom
 * two-pass shader module, not a bigger radius here.
 */
export function withGlow(source: HTMLCanvasElement, radius = 3): HTMLCanvasElement {
  const out = document.createElement("canvas");
  out.width = source.width;
  out.height = source.height;
  const ctx = out.getContext("2d")!;

  ctx.filter = `blur(${radius}px)`;
  ctx.globalCompositeOperation = "lighter";
  ctx.drawImage(source, 0, 0);
  ctx.drawImage(source, 0, 0);   // twice: one pass is barely visible at these alphas

  ctx.filter = "none";
  ctx.globalCompositeOperation = "source-over";
  ctx.drawImage(source, 0, 0);
  return out;
}
```

Apply it in [App.tsx](../web/src/App.tsx) only to the two delta views — `delta` and `air`,
where the signal is sparse and a halo helps the eye find it. Do **not** glow the baseline
LST or the compare view: those are dense fields covering the whole tile, and `lighter` on a
dense field just raises the exposure of everything.

```ts
image: withGlow(toCanvas(colourise(deltaRaster, { … }), deltaRaster)),
```

One caveat to keep in the code: additive compositing brightens overlapping halos, so two
adjacent strong cells glow brighter than either alone. It is decoration around a legend
whose colours are still exact, but it means **the glow is not readable as magnitude**.

### 5.3 Marching ants

`PathStyleExtension({ dash: true })` gives dashes via `getDashArray`, but it has **no dash
offset**, so there is nothing to animate — a common wrong turn. Cycling `getDashArray`
changes dash *length*, which reads as breathing, not marching.

Since the ring is a fixed, short polyline, generate the dashes as geometry and advance the
phase. ~30 lines, no extension, genuinely marching:

```ts
// web/src/map/ants.ts
import type { Position } from "./useDrawnPolygon";

/**
 * Cut a closed ring into alternating dash segments, offset by `phase`.
 *
 * Planar arithmetic on lon/lat. Over a 20 km tile the distortion is a fraction of a
 * dash — this decides where a dash starts, not where a cell is.
 */
export function antSegments(
  ring: Position[],
  dash = 0.0012,
  gap = 0.0009,
  phase = 0,
): Position[][] {
  const period = dash + gap;
  const out: Position[][] = [];
  let travelled = phase % period;

  for (let i = 0; i < ring.length - 1; i++) {
    const [ax, ay] = ring[i]!;
    const [bx, by] = ring[i + 1]!;
    const len = Math.hypot(bx - ax, by - ay);
    if (len === 0) continue;

    for (let d = -travelled; d < len; d += period) {
      const start = Math.max(0, d);
      const end = Math.min(len, d + dash);
      if (end <= start) continue;
      const t0 = start / len;
      const t1 = end / len;
      out.push([
        [ax + (bx - ax) * t0, ay + (by - ay) * t0],
        [ax + (bx - ax) * t1, ay + (by - ay) * t1],
      ]);
    }
    travelled = (travelled + len) % period;
  }
  return out;
}
```

Drive the phase from an rAF clock in `MapView`, throttled — 20 fps is plenty for ants and
costs a fifth of the layer rebuilds:

```tsx
const [phase, setPhase] = useState(0);
useEffect(() => {
  if (!polygonClosed) return;
  const id = setInterval(() => setPhase((p) => (p + 0.00018) % 0.0021), 50);
  return () => clearInterval(id);
}, [polygonClosed]);
```

…then a `PathLayer` over `antSegments([...vertices, vertices[0]!], 0.0012, 0.0009, phase)`
with `getColor: DRAW_LINE` and `widthUnits: "pixels"`. Guard the whole thing on
`prefers-reduced-motion` — a permanently crawling border is exactly the kind of motion that
rule exists for.

### 5.4 Floating layout

`.app` today is `grid-template-columns: 1fr minmax(20rem, 24rem)` — the map is boxed. Make
it a stacking context instead:

```css
.app { position: relative; height: 100vh; overflow: hidden; }
.map { position: absolute; inset: 0; }
```

Then place, all as absolutely-positioned children over the map:

| Element | Position | Notes |
|---|---|---|
| Brand + tile line | `top-5 left-5` | `glass` pill, `{tile.name}, {tile.country} · 201×202 @ 100 m` from `/health` + `/cube/summary` |
| Window + base-layer selects | `top-5 left-1/2 -translate-x-1/2` | window switch runs through `useTransition`, §5.6 |
| Toolbar | `bottom-6 left-1/2 -translate-x-1/2` | Draw / Undo / Close / Clear / Run, §5.5 |
| Results stack | `top-20 right-5 max-h-[calc(100vh-7rem)] overflow-y-auto w-96` | Result → Equity → Air → Brief, **in that order** |
| Legend | `bottom-6 left-5` | |
| Opacity slider | inside the legend panel | it was never worth its own section |

Keep the results order. It is deliberate: equity above air because it is the only output
with no caveat attached — the thermal figure carries the 2.5× hindcast correction and the
air figure carries a different qualification in each season. Ordering is the cheapest way
to say which number the project stands behind.

Panel wrapper, one component, used by all four:

```tsx
// web/src/panels/FloatingPanel.tsx
import { motion } from "motion/react";
import { GLIDE } from "../motion/springs";

export default function FloatingPanel({
  children, className = "",
}: { children: React.ReactNode; className?: string }) {
  return (
    <motion.section
      initial={{ opacity: 0, x: 24, filter: "blur(6px)" }}
      animate={{ opacity: 1, x: 0, filter: "blur(0px)" }}
      exit={{ opacity: 0, x: 24 }}
      transition={GLIDE}
      className={`glass noise p-5 ${className}`}
    >
      {children}
    </motion.section>
  );
}
```

Wrap in `<AnimatePresence>` in `App.tsx` so a cleared scenario animates out instead of
vanishing. Skip draggable panels — `drag` plus `dragConstraints` is two props, but a
dragged panel over a map needs collision, persistence and a reset affordance to not be
annoying. Fixed positions with `AnimatePresence` gets 90 % of the feel. Add dragging when
someone asks.

### 5.5 Toolbar

The brief says "Draw, Undo, Clear". The real state machine in
[useDrawnPolygon.ts](../web/src/map/useDrawnPolygon.ts) has five verbs — `startDrawing`,
`addVertex`, `completePolygon`, `undoVertex`, `clear` — and `canComplete` gates on
`MIN_VERTICES = 3`. The pill shows the buttons valid for the current phase and uses
`layout` so it resizes with a spring rather than jumping:

```tsx
<motion.div layout transition={GLIDE}
  className="glass absolute bottom-6 left-1/2 flex -translate-x-1/2 items-center gap-1 p-1.5">
  <AnimatePresence mode="popLayout">
    {!draw.complete && !draw.drawing && (
      <ToolButton key="draw" icon={PenLine} label="Draw zone" primary onClick={draw.startDrawing} />
    )}
    {draw.drawing && <>
      <span key="count" className="px-3 font-mono text-xs text-white/45">
        {draw.vertices.length} pt{draw.vertices.length === 1 ? "" : "s"}
        {draw.vertices.length < 3 && ` · ${3 - draw.vertices.length} more`}
      </span>
      <ToolButton key="close" icon={Check} label="Close" primary
        disabled={!draw.canComplete} onClick={draw.completePolygon} />
      <ToolButton key="undo" icon={Undo2} disabled={!draw.vertices.length} onClick={draw.undoVertex} />
    </>}
    {draw.complete && <>
      <ToolButton key="run" icon={Play} label={running ? "Simulating…" : "Run simulation"}
        primary disabled={running} onClick={() => void runSimulation()} />
      <ToolButton key="clear" icon={Trash2} onClick={resetScenario} />
    </>}
  </AnimatePresence>
</motion.div>
```

`AnimatePresence mode="popLayout"` matters here: without it the exiting button holds its
slot while the entering one appears beside it and the pill visibly stutters.

The canopy and emission sliders leave the toolbar and live in the command palette's
resolved-plan view plus a small `glass` panel above the toolbar. Both keep their hint text
verbatim — "a ceiling, not a promise", and the one saying 1.0 means the traffic is *gone*,
not electrified, because brake, tyre and road wear are roughly half of road PM2.5.

### 5.6 Command palette

Replace `ScenarioPanel`'s inline text field with a modal, keeping the component's logic —
it already owns presets, the `planner` label, the refusal display, and the resolved-plan
readout with `tree_count` / `max_trees` / cost.

```tsx
// web/src/panels/CommandPalette.tsx  (shape)
export default function CommandPalette({ open, onOpenChange, presets, planner, hasPolygon,
  plan, error, busy, onPreset, onText, onClear }: Props) { … }
```

- Open on `/` or `⌘K`/`Ctrl+K`; bind with a `keydown` listener that bails when
  `event.target` is already an input, or `/` becomes untypable in every other field.
  `event.preventDefault()` on the match, `Escape` closes.
- Backdrop `fixed inset-0 bg-black/55 backdrop-blur-sm`, panel `glass` at
  `top-[22vh] mx-auto w-[min(42rem,92vw)]`, entering with
  `{ opacity: 0, scale: 0.97, y: -8 } → { opacity: 1, scale: 1, y: 0 }` on `GLIDE`.
- Prompt: **"What is the plan?"** Input keeps `dir="auto"` — that one attribute is what
  makes an Urdu sentence lay out right-to-left, and dropping it in a rewrite is easy.
- Presets render as rows below the input, each showing `preset.caveat` — every preset has
  one for a reason and the current panel already surfaces it via `title`.
- **No microphone.** §0.2.
- The refusal must render at full size inside the palette, not as a toast. It is the
  product: a 422 from `/plan` carries the validator's arithmetic, and it arrives *before*
  any core runs.
- A floating `glass` search bar bottom-left of the toolbar opens the same modal for people
  who do not read keyboard hints.

Trigger it from `App.tsx` with the existing `buildPlan` callback unchanged — it already
adopts both levers and the plan's resolved window, which is load-bearing: "in winter" has
to land on a winter window because the same restriction buys 6–9× more under the inversion.

### 5.7 `useTransition` on the window switch

Swapping 2024-summer → 2024-winter refetches a 40,602-cell raster, decodes it and
re-colourises. Marking it non-urgent keeps the pill responsive:

```tsx
const [pending, startTransition] = useTransition();

const selectWindow = (label: string) =>
  startTransition(() => setSelectedWindow(label));
```

Then `<motion.div animate={{ opacity: pending ? 0.45 : 1 }} transition={SNAP}>` around the
overlay-dependent chrome, and `aria-busy={pending}` on the pill.

Worth being precise about what this buys, because it is easy to oversell: `startTransition`
does not make the network fetch faster and `useCubeLayer` already tracks its own `loading`.
What it removes is the synchronous decode-and-colourise blocking the click feedback. Keep
`useCubeLayer`'s loading state; the two answer different questions.

### 5.8 Compare handle

The divider is currently a deck.gl `LineLayer` at a computed longitude
([App.tsx:337](../web/src/App.tsx#L337)) with the split driven by a range input in the
sidebar. Make it a real handle without moving the maths:

- Keep `columnToLongitude` and keep the `LineLayer` — the line **must** stay a fixed line
  of longitude so both halves always describe the same places.
- Overlay a DOM handle positioned by projecting that longitude to screen space:
  `map.project([dividerLongitude, centreLat]).x`, recomputed on the map's `move` event.
- Drag with a `pointerdown` → `pointermove` listener writing `splitFraction` from
  `map.unproject([x, y]).lng` back through the inverse of `columnToLongitude`. Simpler and
  equally correct: drive `splitFraction` from the pointer's fraction across the map
  container width, and let the existing pipeline recompute the longitude. The line is
  authoritative; the handle is a grip.
- Style: `w-px bg-gradient-to-b from-transparent via-white/80 to-transparent` with a
  `shadow-[0_0_18px_rgba(255,255,255,0.5)]`, and a `size-9 rounded-full glass` grip
  centred with a `⇔` icon. `cursor-ew-resize`, `whileDrag={{ scale: 1.12 }}`.
- Keep the range input too, `sr-only` but focusable, so the split stays keyboard-operable.

### 5.9 Equity bars

[EquityPanel.tsx](../web/src/panels/EquityPanel.tsx) is where the brief's animation request
meets three rules the panel already enforces. Preserve all three:

1. Deciles hold a tenth of the **people**, not of the map — so the "even share" marker at
   `evenMarkerPct` stays and means something.
2. A warmed decile is **drawn, not clipped** — `barGeometry` returns `warming` for a
   negative share and the bar must remain visible.
3. When `shares_reliable` is false, **no bars at all**. The verdict already says "no net
   effect to share out"; animating ten bars off a vanishing denominator would be the exact
   failure the flag exists to prevent.

```tsx
<motion.span
  className={`equity__bar${warming ? " equity__bar--warming" : ""}`}
  initial={{ width: 0 }}
  animate={{ width: `${widthPct}%` }}
  transition={{ ...SETTLE, delay: decile.decile * 0.045 }}
/>
```

The pulse the brief asks for is already computable — `WASTED_THRESHOLD = 0.2` in
[equity.ts](../web/src/panels/equity.ts) is the same 20 % boundary, and `equityVerdict`
already returns `tone: "wasted"` above it. Drive the pulse off the tone rather than
re-deriving the threshold:

```tsx
<motion.p
  animate={verdict.tone === "wasted" ? { opacity: [1, 0.55, 1] } : { opacity: 1 }}
  transition={verdict.tone === "wasted" ? { duration: 2.2, repeat: Infinity } : SNAP}
  className={verdict.tone === "wasted" ? "text-red-400" : ""}
>
```

`equity__row`, `equity__bar` and `equity__bar--warming` must survive as class names —
see §6.

---

## 6. What this can break

Four regressions, all cheap to avoid and all silent if you do not:

**The tests do not use jsdom.** Every panel test renders through
`renderToStaticMarkup` from `react-dom/server` — deliberately, so no testing-library and
no jsdom is needed. Two consequences: `motion` components must stay SSR-safe (they are —
`whileInView` and `whileHover` are no-ops on the server, `initial` renders), and
`EquityPanel.test.tsx:50` asserts `html.match(/equity__row/g)` has length 10. Converting
those bars to pure Tailwind classes fails that test. Keep the semantic class names
alongside the utilities.

**Text assertions.** `ScenarioPanel.test.tsx` checks for `"rules (no model configured)"`,
`"not calibrated"`, `"75,240"`, `"12.54 km²"` and `"disabled"`. `BriefDocument.test.tsx`
checks for `"never reports high"` and `"not a measurement of what happened"`. Moving that
copy into a palette is fine; deleting or rewording it is a test failure and, more to the
point, a caveat going missing.

**The print stylesheet.** `@media print` at [App.css:590](../web/src/App.css#L590) hides
`.app > .map` and `.app > .sidebar` by direct-child selector. Removing `.sidebar` and
floating panels instead means those panels print. Update the rule to hide everything
except `.doc` — `.app > *:not(.doc) { display: none !important; }` — and re-verify by
printing, because the council brief is a shipped feature and this is the only way to catch
it.

**`BriefDocument` must stay out of the glass.** It renders black-on-white for paper. Do not
give it `glass`, `noise`, `backdrop-filter` or an accent colour; browsers drop
`backdrop-filter` in print and a "frosted" brief prints as a grey box.

Also: `backdrop-filter` over a moving MapLibre canvas is the most expensive thing in this
revamp. If panning stutters, drop `blur(16px)` to `blur(10px)` before touching anything
else — visually near-identical, materially cheaper.

---

## 7. Order of work

Each step ends green. Do not stack two before verifying.

| # | Step | Verify |
|---|---|---|
| 1 | Tailwind + fonts + tokens (§1, §2), `App.css` untouched | `npm run dev` — app looks the same, new fonts |
| 2 | Route split (§3), `Landing.tsx` a stub | `#/` stub, `#/app` dashboard, three.js not in the dashboard chunk |
| 3 | Hero + terrain (§4.1) | 60 fps at `dpr` 1.75; reduced-motion path renders |
| 4 | Bento + sticky simulators + palette mock + outro (§4.2–4.5) | copy matches §0.4 and the table in §4.3 |
| 5 | Dashboard layout → floating (§5.4, §5.5) | `npm run test` green; **print a brief** |
| 6 | Command palette (§5.6) | `/` and ⌘K; a refusal renders in full |
| 7 | Map rendering: extrusion, glow, ants (§5.1–5.3) | ΔLST zero-cells still transparent |
| 8 | Equity bars, compare handle, `useTransition` (§5.7–5.9) | `EquityPanel.test.tsx` still counts 10 rows |

```bash
cd web
npm run test     # vitest: raster decode, ramps, compare split, equity, scenario, brief
npm run lint     # oxlint
npm run build    # tsc -b + production bundle
npm run dev      # :5173 — the API's CORS allowlist is 5173 only. Free the port; do not
                 # accept Vite's fallback.
```

---

## 8. Deferred, on purpose

- **Draggable panels.** Fixed positions with `AnimatePresence` get most of the feel;
  dragging needs collision, persistence and a reset to not be annoying (§5.4).
- **True deck.gl bloom.** A custom two-pass luma shader module. The Canvas2D composite in
  §5.2 is ~15 lines and its only real limitation is that the halo scales with zoom.
- **Light theme.** Dropped, not forgotten. A frosted dark shell over pale Positron is one
  design; supporting both is two.
- **`react-router`.** Add it at the third route.
- **Real Lahore elevation in the hero.** Needs a build-time asset export and a DSM caveat
  in the copy (§4.1).
