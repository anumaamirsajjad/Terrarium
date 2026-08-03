/**
 * Point MapLibre at a worker Vite has actually emitted.
 *
 * MapLibre resolves its tile-parsing worker as
 * `new URL('./maplibre-gl-worker.mjs', import.meta.url)`. Under a bundler that is not a
 * statically analysable reference, so Vite never emits the file; at runtime
 * `import.meta.url` is the hashed app chunk and the worker 404s.
 *
 * **The failure is almost silent, which is why this file exists.** The style, the
 * TileJSON, the sprites and the glyphs all fetch with 200s, nothing throws, no console
 * error appears — and MapLibre simply never requests a single `.pbf`, because tile
 * loading is dispatched through the worker. What you see is an empty basemap that looks
 * like a style or a CORS problem and is neither.
 *
 * `?worker&url` makes Vite bundle the worker properly — following its import of the
 * shared chunk, which a plain `?url` copy would leave dangling — and hand back the
 * emitted URL. A plain `?url` import is the tempting shorter fix and produces a worker
 * that 404s on its own dependency instead.
 */

import { setWorkerUrl } from "maplibre-gl";
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";

setWorkerUrl(maplibreWorkerUrl);
