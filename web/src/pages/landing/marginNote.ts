/**
 * The margin rail's state: the contexts and the hook sections use to claim it.
 *
 * Split from the rail's own components so each file exports one kind of thing — mixing a
 * hook and a component in one module breaks fast refresh for both.
 *
 * **Two contexts, not one, and that is load-bearing.** A single context carrying both the
 * active note and the claim/release pair is an infinite loop: claiming changes the value,
 * the new value changes the context identity, the subscribing effect tears down and
 * releases, and re-running it claims again. Splitting the stable callbacks away from the
 * changing note means the hook subscribes only to something that never changes.
 */

import { useInView } from "motion/react";
import { createContext, useContext, useEffect, useRef } from "react";

export interface MarginNote {
  /** What the note qualifies — the figure or the claim, not the section. */
  subject: string;
  body: string;
}

export interface RailDispatch {
  claim: (id: string, note: MarginNote) => void;
  release: (id: string) => void;
}

/** Stable for the provider's whole life. Safe to depend on. */
export const RailDispatchContext = createContext<RailDispatch | null>(null);

/** Changes as the reader scrolls. Only the rail itself subscribes. */
export const RailNoteContext = createContext<MarginNote | null>(null);

/**
 * Attach a note to a section. Returns the ref to put on it.
 *
 * `amount: 0.4` rather than "any part visible": the note should arrive when the section
 * is being read, not when its first pixel crosses the fold.
 */
export function useMarginNote<T extends HTMLElement = HTMLElement>(
  id: string,
  note: MarginNote,
) {
  // Generic over the element: the ref lands on a <section> in most callers and a <div> in
  // the sticky ones, and React's ref types are invariant.
  const ref = useRef<T>(null);
  const inView = useInView(ref, { amount: 0.4 });
  const dispatch = useContext(RailDispatchContext);

  // The note is written inline at every call site, so it is a new object on every render.
  // Holding it in a ref keeps it out of the dependency array.
  const latest = useRef(note);
  latest.current = note;

  useEffect(() => {
    if (!dispatch) return;
    if (!inView) {
      dispatch.release(id);
      return;
    }
    dispatch.claim(id, latest.current);
    return () => dispatch.release(id);
  }, [inView, id, dispatch]);

  return ref;
}
