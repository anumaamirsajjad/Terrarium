/**
 * The page's apparatus: a margin that argues with the body text.
 *
 * Terrarium's whole epistemic personality is stating its own error. The emulator
 * over-predicts by 2.5x and says so. `uncertainties` is never empty. `confidence` has no
 * "high". The DSL refuses a plan rather than quietly trimming it. A landing page that
 * makes claims in the usual way and buries the qualifications in a footer would be
 * describing a different, worse piece of software.
 *
 * So every section carries a note, the note is real, and it tracks the reader down the
 * page in the margin — the way a correction sits beside the line it corrects, not at the
 * end of the document where nobody reaches it.
 *
 * Hidden below `xl`. A caveat crushed into a phone's gutter is a caveat nobody reads, so
 * on narrow screens the sections carry their qualifications inline instead.
 */

import { AnimatePresence, motion } from "motion/react";
import { useCallback, useContext, useMemo, useRef, useState } from "react";

import {
  type MarginNote,
  RailDispatchContext,
  RailNoteContext,
  type RailDispatch,
} from "./marginNote";

export function MarginRailProvider({ children }: { children: React.ReactNode }) {
  // A stack, not a single value: two sections overlap briefly mid-scroll, and the last to
  // arrive should win without the earlier one's exit clearing it.
  const stack = useRef<{ id: string; note: MarginNote }[]>([]);
  const [note, setNote] = useState<MarginNote | null>(null);

  const sync = useCallback(() => {
    setNote(stack.current.at(-1)?.note ?? null);
  }, []);

  // Both callbacks are stable for the provider's life, which is what makes the hook's
  // effect safe to depend on them. See the note in marginNote.ts.
  const dispatch = useMemo<RailDispatch>(
    () => ({
      claim(id, next) {
        stack.current = [...stack.current.filter((entry) => entry.id !== id), { id, note: next }];
        sync();
      },
      release(id) {
        stack.current = stack.current.filter((entry) => entry.id !== id);
        sync();
      },
    }),
    [sync],
  );

  return (
    <RailDispatchContext.Provider value={dispatch}>
      <RailNoteContext.Provider value={note}>{children}</RailNoteContext.Provider>
    </RailDispatchContext.Provider>
  );
}

export function MarginRail() {
  const note = useContext(RailNoteContext);

  return (
    <aside
      aria-live="polite"
      className="pointer-events-none fixed top-1/2 right-8 z-30 hidden w-56 -translate-y-1/2 xl:block"
    >
      <AnimatePresence mode="wait">
        {note && (
          <motion.div
            key={note.subject}
            initial={{ opacity: 0, x: 12 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -8 }}
            transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
            className="border-l border-white/15 pl-4"
          >
            <p className="font-mono text-[0.6rem] tracking-[0.2em] text-white/30 uppercase">
              What this does not prove
            </p>
            <p className="text-shoal/90 mt-3 font-mono text-[0.7rem] leading-relaxed">
              {note.subject}
            </p>
            <p className="mt-2 text-[0.72rem] leading-relaxed text-white/45">{note.body}</p>
          </motion.div>
        )}
      </AnimatePresence>
    </aside>
  );
}
