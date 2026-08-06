/**
 * Citizen photos: submit one, see what the model made of it.
 *
 * Two things this panel is careful about, both inherited from the API:
 *
 * 1. **These are reports, not measurements.** They render on the same grid as the modelled
 *    layers precisely so they can be compared, which is exactly why the label has to be on
 *    screen rather than in a doc comment.
 * 2. **A 503 here is not a bug.** This is the one path with no offline fallback — no rule
 *    parser can read a photograph — so a deployment without a vision key shows the reason
 *    and the rest of the product carries on.
 *
 * The photo never leaves the browser except as the base64 body of one request, and the
 * coordinates are the ones the user chose on the map: nothing reads EXIF, so a photo
 * cannot silently report where it was actually taken.
 */

import { useRef, useState } from "react";

import { type ObservationCategory, type StoredObservation, api } from "../api/client";
import { MAX_PHOTO_BYTES, readAsDataUri, stripDataUri } from "./photo";

export interface ObservationsPanelProps {
  /** Where the photo is about. The map's last click, or the tile centre. */
  lon: number;
  lat: number;
  observations: StoredObservation[];
  reader: string;
  onSubmitted: () => void;
}

const CATEGORY_LABELS: Record<ObservationCategory, string> = {
  shade_deficit: "No shade",
  canopy: "Canopy",
  air_source: "Air source",
  other: "Other",
};

export default function ObservationsPanel({
  lon,
  lat,
  observations,
  reader,
  onSubmitted,
}: ObservationsPanelProps) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const configured = !reader.startsWith("no vision model");

  async function submit(file: File): Promise<void> {
    if (file.size > MAX_PHOTO_BYTES) {
      setError(`That photo is ${(file.size / 1_048_576).toFixed(1)} MB. Keep it under 5 MB.`);
      return;
    }

    setBusy(true);
    setError(null);
    try {
      const dataUri = await readAsDataUri(file);

      await api.submitObservation({
        image_base64: stripDataUri(dataUri),
        mime_type: file.type || "image/jpeg",
        lon,
        lat,
      });
      onSubmitted();
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <section className="control observations" aria-labelledby="observations-heading">
      <h2 id="observations-heading">Citizen photos</h2>

      <p className="hint">
        A photo of a street, read into a typed observation and placed on the grid at{" "}
        {lat.toFixed(4)}, {lon.toFixed(4)} — click the map to move that. <strong>Reports,
        not measurements:</strong> they sit beside the cube and never inside it.
      </p>

      <input
        ref={input}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        disabled={busy || !configured}
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void submit(file);
        }}
      />

      {!configured && (
        <p className="hint">
          No vision model is configured, so photos cannot be read. This is the one feature
          here with no offline fallback — nothing else in Terrarium needs a key.
        </p>
      )}
      {busy && <p className="hint">Reading the photo…</p>}
      {error && <p className="error-text">{error}</p>}

      {observations.length > 0 && (
        <ul className="observations__list">
          {observations.map((item) => (
            <li key={item.id}>
              <span className={`observations__tag observations__tag--${item.observation.category}`}>
                {CATEGORY_LABELS[item.observation.category]}
              </span>
              <span className="observations__severity">
                severity {item.observation.severity}/5
              </span>
              <span className="muted"> · confidence {item.observation.confidence.toFixed(2)}</span>
              <p className="observations__description">{item.observation.description}</p>
            </li>
          ))}
        </ul>
      )}

      {observations.length > 0 && (
        <p className="hint">
          Read by <code>{reader}</code>. Kept in the server&rsquo;s memory only — they are
          gone when it restarts.
        </p>
      )}
    </section>
  );
}
