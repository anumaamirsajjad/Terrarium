/**
 * A resized canvas and a render loop, for the three simulator visualisations.
 *
 * Plain 2D canvas rather than a second R3F scene: these are grids, particles and bars,
 * and mounting another WebGL context beside the hero's is how a landing page starts
 * dropping frames on the machine it will actually be demoed on.
 */

import { useEffect, useRef } from "react";

export type DrawFn = (
  ctx: CanvasRenderingContext2D,
  size: { width: number; height: number },
  elapsed: number,
) => void;

/**
 * A scratch canvas for building a field pixel by pixel.
 *
 * `putImageData` ignores the context transform and writes in device pixels, so writing a
 * low-resolution field straight onto a DPR-scaled canvas puts it in the wrong place at the
 * wrong size. Staging it here and blitting with `drawImage` respects the transform and
 * gets the smooth upscale for free.
 */
export function scratchCanvas(width: number, height: number): CanvasRenderingContext2D {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  return canvas.getContext("2d")!;
}

/**
 * @param draw    called once per frame with CSS-pixel dimensions; the context is already
 *                scaled for device pixel ratio, so draw in CSS pixels and ignore DPR.
 * @param running false pauses the loop after one frame — the visualisation stays on
 *                screen, it just stops advancing. Off-pane and reduced-motion both use it.
 */
export function useCanvasLoop(draw: DrawFn, running: boolean) {
  const ref = useRef<HTMLCanvasElement>(null);
  // The draw function closes over props and changes identity every render. Holding it in
  // a ref keeps the loop from being torn down and restarted sixty times a second.
  const latest = useRef(draw);
  latest.current = draw;

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let frame = 0;
    const start = performance.now();
    let size = { width: 0, height: 0 };

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const box = canvas.getBoundingClientRect();
      size = { width: box.width, height: box.height };
      canvas.width = Math.round(box.width * dpr);
      canvas.height = Math.round(box.height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    resize();

    const tick = (now: number) => {
      // Two of the three panes are `display: none` at any moment, so their canvases
      // measure zero. Drawing into one is wasted work, and any `drawImage` involving a
      // zero-sized canvas throws InvalidStateError outright.
      if (size.width > 0 && size.height > 0) {
        latest.current(ctx, size, (now - start) / 1000);
      }
      if (running) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [running]);

  return ref;
}
