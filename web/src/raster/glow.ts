/**
 * A halo under the raster, for the two views where the signal is sparse.
 *
 * **deck.gl cannot do this natively.** `PostProcessEffect` takes luma.gl shader modules and
 * luma.gl v9's image-processing set has no bloom or glow pass — it ships `brightnessContrast`,
 * `triangleBlur`, `zoomBlur`, `hueSaturation`, `vibrance`, `vignette`, `tiltShift`, `ink`,
 * `dotScreen`, `edgeWork` and friends. `PostProcessEffect(triangleBlur)` would blur the
 * raster, which is the opposite of the requirement: nearest-neighbour sampling is deliberate
 * in `MapView`, because at 100 m a cell is a measured unit and smoothing invents gradients
 * the cube does not contain.
 *
 * So the glow is composited into the overlay canvas before deck.gl ever sees it. The crisp
 * copy is drawn last and unfiltered, so every nearest-neighbour edge the map depends on is
 * preserved exactly; only the halo around it is soft.
 *
 * **The glow is not readable as magnitude.** `lighter` is additive, so two adjacent strong
 * cells glow brighter than either alone. It is decoration around a legend whose colours are
 * still exact, and the legend remains the only thing that encodes a value.
 *
 * ponytail: the blur radius is in *cell* space, so the halo scales with zoom instead of
 * staying a fixed number of screen pixels. Fine for a 20 km tile at one zoom range. If it
 * ever needs to be zoom-invariant that is a real `PostProcessEffect` with a custom two-pass
 * shader module, not a bigger radius here.
 */

export function withGlow(source: HTMLCanvasElement, radius = 3): HTMLCanvasElement {
  const out = document.createElement("canvas");
  out.width = source.width;
  out.height = source.height;

  const ctx = out.getContext("2d");
  if (!ctx) return source;

  ctx.filter = `blur(${radius}px)`;
  ctx.globalCompositeOperation = "lighter";
  ctx.drawImage(source, 0, 0);
  // Twice: one pass is barely visible at the alphas a sparse delta carries.
  ctx.drawImage(source, 0, 0);

  ctx.filter = "none";
  ctx.globalCompositeOperation = "source-over";
  ctx.drawImage(source, 0, 0);
  return out;
}
