# Terrarium — web

React 19 + Vite frontend for the Terrarium digital twin. MapLibre GL renders the
basemap; deck.gl renders the data overlays.

```bash
npm install
npm run dev      # http://localhost:5173
npm run build    # type-check + production bundle
npm run test     # vitest — the pure raster/geometry logic
npm run lint     # oxlint
```

The API must be running (`uv run terrarium-api` from the repository root) or the page
shows a connection error. To point elsewhere, copy `.env.example` to `.env.local` and set
`VITE_API_URL`. The API's CORS allowlist is `localhost:5173` and `127.0.0.1:5173`, so a
dev server that falls back to another port will be blocked — use `--strictPort` and free
5173 rather than accepting the fallback.

`src/api/client.ts` mirrors the Pydantic response models in `src/terrarium/api/schemas/`
**by hand**. When a schema changes on the Python side, change it here too — or generate
from `/openapi.json`.

## How it fits together

| module | does |
|---|---|
| `api/client.ts` | typed fetchers; surfaces the API's own `detail` on errors |
| `raster/decode.ts` | base64 float32 → `Float32Array`, encoding checked not assumed |
| `raster/ramp.ts` | sequential + diverging ramps; the diverging one is transparent at 0 |
| `raster/image.ts` | colourising, the compare split, extents |
| `map/MapView.tsx` | MapLibre + deck.gl overlay, drawing layers |
| `map/useDrawnPolygon.ts` | click-to-draw polygon state |
| `panels/` | legend and the result readout |

## Two things that will bite you

**The basemap goes blank in a near-silent way.** MapLibre parses vector tiles in a web
worker and resolves it from `import.meta.url`, which no bundler can emit. When it 404s,
the style, TileJSON, sprites and glyphs all still fetch with 200s, nothing throws, and the
map simply never requests a single `.pbf`. `src/map/maplibreWorker.ts` pins the worker URL
explicitly and `vite.config.ts` sets `worker.format = 'es'`; both are required.

**Basemap tiles are OpenFreeMap Positron and must stay that way.** MapLibre GL is the free
*library*; tiles are a separate service. MapTiler and Stadia — what most tutorials use —
meter usage behind an API key, and this project must run without a credit card (D13). That
is a claim in the pitch, not just a budget line.
