/**
 * Decode the API's raster payload.
 *
 * The wire format is deliberately named in the payload rather than assumed
 * (`base64:float32:little:row-major`), so we check it. If the server ever changes
 * encoding, this throws a legible error instead of rendering plausible garbage — a
 * silently misread float array looks like a real map with the wrong colours on it.
 */

import type { LayerResponse } from "../api/client";

export const EXPECTED_ENCODING = "base64:float32:little:row-major";

/** A decoded raster: values plus the shape needed to index them. */
export interface Raster {
  values: Float32Array;
  /** Number of rows. Row 0 is the NORTH edge — y descends, raster convention. */
  height: number;
  width: number;
}

function base64ToBytes(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

/**
 * Turn a `LayerResponse` into a typed array.
 *
 * NaN survives the round trip and means no-data. It must stay NaN rather than becoming
 * 0: zero is a real temperature and a real ΔLST, so collapsing the two would paint
 * missing data as "no change".
 */
export function decodeLayer(layer: LayerResponse): Raster {
  if (layer.encoding !== EXPECTED_ENCODING) {
    throw new Error(
      `unexpected raster encoding "${layer.encoding}"; ` +
        `this client only reads "${EXPECTED_ENCODING}"`,
    );
  }

  const [height, width] = layer.grid.shape;
  const bytes = base64ToBytes(layer.data);
  const expectedBytes = height * width * 4;

  if (bytes.byteLength !== expectedBytes) {
    throw new Error(
      `raster is ${bytes.byteLength} bytes but ${height}x${width} float32 needs ${expectedBytes}`,
    );
  }

  // Little-endian is what the contract promises and what every platform we target is,
  // so a plain Float32Array view is correct. The byteOffset is 0 by construction.
  return { values: new Float32Array(bytes.buffer), height, width };
}
