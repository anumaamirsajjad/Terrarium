/**
 * The plan, as a command palette.
 *
 * A modal shell around `ScenarioPanel` rather than a rewrite of it: that component already
 * owns the presets, the parser label, the refusal display and the resolved-plan readout,
 * and it is the one with a test asserting that the unflattering strings — "not calibrated",
 * "rules (no model configured)" — are still in the markup. Wrapping keeps all of that and
 * costs a few lines.
 *
 * **The refusal renders at full size, inside the palette, not as a toast.** A 422 from
 * `/plan` carries the validator's arithmetic and arrives *before* any core runs; it is the
 * product, and a product does not belong in a corner for four seconds.
 *
 * **No microphone.** Voice capture was removed on 2026-08-07 along with the photo route —
 * they were the only two features that could not be defended offline, and the photo route
 * was the single endpoint that required an API key. Putting a mic button back into this
 * modal would advertise a feature that does not exist and would trade the strongest claim
 * the project has for it.
 */

import { AnimatePresence, motion } from "motion/react";
import { useEffect } from "react";

import { GLIDE } from "../motion/springs";
import ScenarioPanel, { type ScenarioPanelProps } from "./ScenarioPanel";

export interface CommandPaletteProps extends ScenarioPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Should a keystroke reach the palette, or the thing the user is typing into?
 *
 * Without this check `/` becomes untypable in every other field on the page — including
 * the palette's own input, which is the failure that makes the shortcut worse than useless.
 */
function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    target.tagName === "INPUT" ||
    target.tagName === "TEXTAREA" ||
    target.tagName === "SELECT"
  );
}

export default function CommandPalette({
  open,
  onOpenChange,
  ...scenario
}: CommandPaletteProps) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && open) {
        onOpenChange(false);
        return;
      }
      if (isTyping(event.target)) return;
      const shortcut = event.key === "/" || ((event.metaKey || event.ctrlKey) && event.key === "k");
      if (!shortcut) return;
      event.preventDefault();
      onOpenChange(!open);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.16 }}
          className="fixed inset-0 z-50 bg-black/55 backdrop-blur-sm"
          onPointerDown={(event) => {
            if (event.target === event.currentTarget) onOpenChange(false);
          }}
        >
          <motion.div
            initial={{ opacity: 0, scale: 0.97, y: -8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.97, y: -8 }}
            transition={GLIDE}
            role="dialog"
            aria-modal="true"
            aria-label="What is the plan?"
            className="glass noise mx-auto mt-[16vh] max-h-[68vh] w-[min(44rem,92vw)]
                       overflow-y-auto p-6"
          >
            <div className="mb-4 flex items-baseline justify-between gap-4">
              <h2 className="font-mono text-lg tracking-tight text-white">
                What is the plan?
              </h2>
              <kbd className="rounded border border-white/15 px-1.5 py-0.5 font-mono
                              text-[0.6rem] text-white/40">
                esc
              </kbd>
            </div>

            {/* The panel keeps its own markup and its own class names — see its test. */}
            <ScenarioPanel {...scenario} />
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
