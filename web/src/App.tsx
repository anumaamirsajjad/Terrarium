import { useEffect, useState } from "react";
import { api, API_BASE, type HealthResponse } from "./api/client";
import "./App.css";

type Status =
  | { kind: "loading" }
  | { kind: "ok"; data: HealthResponse }
  | { kind: "error"; message: string };

export default function App() {
  const [status, setStatus] = useState<Status>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;

    api
      .health()
      .then((data) => {
        if (!cancelled) setStatus({ kind: "ok", data });
      })
      .catch((error: Error) => {
        if (!cancelled) setStatus({ kind: "error", message: error.message });
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="shell">
      <header>
        <h1>Terrarium</h1>
        <p className="tagline">
          Neighbourhood-scale digital twin for climate interventions
        </p>
      </header>

      <section className="panel">
        <h2>API connection</h2>

        {status.kind === "loading" && (
          <p className="muted">Connecting to {API_BASE}…</p>
        )}

        {status.kind === "error" && (
          <div className="state state--error">
            <p>
              <strong>Cannot reach the API.</strong> {status.message}
            </p>
            <p className="muted">
              Start it with <code>uv run terrarium-api</code>, then reload.
            </p>
          </div>
        )}

        {status.kind === "ok" && (
          <div className="state state--ok">
            <p className="connected">
              <span className="dot" />
              Connected — {status.data.service} v{status.data.version}
              <span className="badge">{status.data.env}</span>
            </p>
            <dl>
              <dt>Tile</dt>
              <dd>
                {status.data.tile.name}, {status.data.tile.country}
              </dd>

              <dt>Bounding box</dt>
              <dd>
                <code>
                  [{status.data.tile.bbox.map((n) => n.toFixed(4)).join(", ")}]
                </code>
              </dd>

              <dt>Centroid</dt>
              <dd>
                <code>
                  {status.data.tile.centroid[1].toFixed(4)}&nbsp;N,{" "}
                  {status.data.tile.centroid[0].toFixed(4)}&nbsp;E
                </code>
              </dd>

              <dt>Analysis grid</dt>
              <dd>
                {status.data.tile.crs} @ {status.data.tile.target_resolution_m}&nbsp;m
              </dd>
            </dl>
          </div>
        )}
      </section>

      <footer className="muted">
        Skeleton only — no map layer or physics core wired up yet.
      </footer>
    </main>
  );
}
