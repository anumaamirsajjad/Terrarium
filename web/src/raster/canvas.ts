/**
 * Coloured bytes → a texture source deck.gl can upload.
 *
 * A `<canvas>` rather than raw `ImageData`: canvases are a universally accepted texture
 * source across luma.gl versions, and `putImageData` is synchronous, so the overlay can
 * be produced inside a `useMemo` without an async round trip and the accompanying frame
 * where the map shows the previous scenario's colours.
 */

import type { Raster } from "./decode";
import type { RgbaBytes } from "./image";

export function toCanvas(rgba: RgbaBytes, raster: Raster): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = raster.width;
  canvas.height = raster.height;

  const context = canvas.getContext("2d");
  if (!context) throw new Error("could not get a 2d context to build the raster overlay");

  context.putImageData(new ImageData(rgba, raster.width, raster.height), 0, 0);
  return canvas;
}
