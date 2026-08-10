/**
 * Is the viewport at Tailwind's `lg` breakpoint or wider?
 *
 * Three sections on this page pin a full-height pane and scrub an animation off its
 * scroll. That is a layout the `lg:` prefix can turn off in CSS, but the *scroll wiring*
 * behind it cannot be expressed in CSS at all: the progress has to come from a different
 * element once the pin is gone, or the animation runs against a track that is barely
 * taller than the pane and finishes before the visualisation is on screen. Which is
 * exactly what happened — on a phone the seven sources arrived already collapsed into the
 * cube, and nobody ever saw the state the section is about.
 *
 * So this is the one thing on the page that reads the breakpoint in JS. Live, not
 * once-at-mount: a resize across it swaps which element drives the scrub, and getting that
 * wrong leaves a section frozen at whatever value it held when the window changed.
 *
 * 1024px is `lg`. Duplicated from Tailwind's scale rather than imported, because the
 * config is CSS-first here and there is nothing to import it from.
 */

import { useEffect, useState } from "react";

const LG = "(min-width: 1024px)";

export function useIsWide(): boolean {
  const [wide, setWide] = useState(
    () => typeof window !== "undefined" && window.matchMedia(LG).matches,
  );

  useEffect(() => {
    const query = window.matchMedia(LG);
    const onChange = () => setWide(query.matches);
    query.addEventListener("change", onChange);
    // The initial state is read during the first render, which on a hydrated page can be
    // before the real viewport is known. Re-reading here costs nothing and closes that gap.
    onChange();
    return () => query.removeEventListener("change", onChange);
  }, []);

  return wide;
}
