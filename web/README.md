# Terrarium — web

React 19 + Vite frontend for the Terrarium digital twin. MapLibre GL renders the
basemap; deck.gl renders the data overlays.

```bash
npm install
npm run dev      # http://localhost:5173
npm run build    # type-check + production bundle
npm run lint     # oxlint
```

The API must be running (`uv run terrarium-api` from the repository root) or the page
shows a connection error. To point elsewhere, copy `.env.example` to `.env.local` and set
`VITE_API_URL`.

`src/api/client.ts` mirrors the Pydantic response models in `src/terrarium/api/schemas/`
**by hand**. When a schema changes on the Python side, change it here too — or generate
from `/openapi.json`.

Current state: the page calls `/health` and renders the active tile. deck.gl and MapLibre
are installed but not yet wired up — see
[`docs/IMPLEMENTATION_PLAN.md`](../docs/IMPLEMENTATION_PLAN.md), Phase 3.
