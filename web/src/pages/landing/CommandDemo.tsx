/**
 * The intervention language, as a command prompt.
 *
 * Two things this section deliberately does **not** advertise: a microphone and a camera.
 * Voice capture and citizen photos were both built, both worked, and both were removed on
 * 2026-08-07 — they were the only two features that could not be defended offline, and the
 * photo route was the single endpoint in the project that required an API key. Cutting
 * them is what makes the keyless claim unconditional, so putting them back into the copy
 * would trade the strongest thing this page can say for a feature that no longer exists.
 *
 * What is left is real, and testable: English and Urdu, digits folded, no model required.
 */

import { motion, useReducedMotion } from "motion/react";
import { useEffect, useState } from "react";

import { useMarginNote } from "./marginNote";
import { Eyebrow, InlineCaveat, Rule } from "./primitives";

const LINES = [
  { text: "Plant 5,000 trees near the Canal", dir: "ltr" as const },
  { text: "سردیوں میں گاڑیوں پر پابندی", dir: "rtl" as const },
];

const TYPE_MS = 45;
const DELETE_MS = 25;
const HOLD_MS = 2200;

const NOTE = {
  subject: "The parser is a front door, not a safety mechanism.",
  body: "Nothing about reading a sentence makes a plan safe. What makes it safe is that whatever produces one — a model, a preset button, a regex — it is re-validated as a plan and then against the tile before a core sees a number.",
};

export default function CommandDemo() {
  const section = useMarginNote("dsl", NOTE);
  const reduced = useReducedMotion();
  const [line, setLine] = useState(0);
  const [count, setCount] = useState(0);
  const [deleting, setDeleting] = useState(false);

  const full = LINES[line]!;

  useEffect(() => {
    if (reduced) return;

    if (!deleting && count === full.text.length) {
      const hold = setTimeout(() => setDeleting(true), HOLD_MS);
      return () => clearTimeout(hold);
    }
    if (deleting && count === 0) {
      setDeleting(false);
      setLine((current) => (current + 1) % LINES.length);
      return;
    }
    const step = setTimeout(
      () => setCount((c) => c + (deleting ? -1 : 1)),
      deleting ? DELETE_MS : TYPE_MS,
    );
    return () => clearTimeout(step);
  }, [count, deleting, full.text.length, reduced]);

  // One text node, sliced by an index — never one element per character. Urdu is a
  // cursive script: splitting it into separate spans breaks the joins between letters and
  // renders the sentence as disconnected forms.
  const shown = reduced ? full.text : full.text.slice(0, count);

  return (
    <section ref={section} className="mx-auto max-w-4xl px-8 py-32">
      <Eyebrow>dsl/planner.py</Eyebrow>
      <h2 className="mt-6 font-mono text-4xl tracking-tight text-white md:text-5xl">
        Say what you want to build.
      </h2>
      <Rule className="mt-8" />

      <motion.div
        initial={{ opacity: 0, y: 24 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, amount: 0.4 }}
        className="glass noise mt-12 overflow-hidden"
      >
        <div className="flex items-center gap-2 border-b border-white/10 px-5 py-3">
          <span className="size-2.5 rounded-full bg-white/10" />
          <span className="size-2.5 rounded-full bg-white/10" />
          <span className="size-2.5 rounded-full bg-white/10" />
          <span className="ml-2 font-mono text-[0.7rem] text-white/30">
            What is the plan?
          </span>
          <kbd
            className="ml-auto rounded border border-white/15 px-2 py-0.5 font-mono text-[0.65rem]
                       text-white/40"
          >
            ⌘K
          </kbd>
        </div>

        <div className="flex min-h-[6rem] items-center px-6 py-7">
          <p
            dir={full.dir}
            lang={full.dir === "rtl" ? "ur" : "en"}
            className="w-full text-2xl tracking-tight text-white md:text-3xl"
          >
            {shown}
            {!reduced && (
              <motion.span
                animate={{ opacity: [1, 1, 0, 0] }}
                transition={{ duration: 1, repeat: Infinity, times: [0, 0.5, 0.5, 1] }}
                className="ml-0.5 inline-block h-[1.1em] w-[2px] translate-y-[0.15em] bg-shoal"
              />
            )}
          </p>
        </div>

        <div className="grid gap-px border-t border-white/10 bg-white/5 sm:grid-cols-3">
          {[
            ["Parsed by", "rules (no model configured)"],
            ["Resolved to", "canopy + emission fractions"],
            ["Checked against", "the polygon you drew"],
          ].map(([label, value]) => (
            <div key={label} className="bg-shell/70 px-5 py-4">
              <dt className="font-mono text-[0.65rem] tracking-wider text-white/30 uppercase">
                {label}
              </dt>
              <dd className="mt-1 font-mono text-xs text-white/60">{value}</dd>
            </div>
          ))}
        </div>
      </motion.div>

      <div className="mt-10 grid gap-6 text-sm leading-relaxed text-white/50 md:grid-cols-2">
        <p>
          Typed in English or Urdu. Eastern Arabic-Indic digits are folded to ASCII before
          any pattern runs — <span className="font-mono text-white/75">۵۰۰۰</span> matches no{" "}
          <code className="font-mono text-white/75">\d</code>, and without that pass an Urdu
          sentence parses as a plan with no quantity in it.
        </p>
        <p>
          A language model is optional and lives in exactly one file. With no key set, the
          same sentence is read by a deterministic rule parser and the panel says which one
          ran. Either way the plan is re-validated against the polygon before a simulator
          sees a number.
        </p>
      </div>

      <InlineCaveat note={NOTE} />

      <motion.blockquote
        initial={{ opacity: 0 }}
        whileInView={{ opacity: 1 }}
        viewport={{ once: true }}
        className="mt-10 border-l-2 border-red-500/60 py-2 pl-6"
      >
        <p className="font-mono text-sm leading-relaxed text-red-300/90">
          422 · 5,000 trees need 0.125 km² of crown at 25 m² each, but this polygon has only
          0.031 km² still plantable.
        </p>
        <footer className="mt-3 text-xs text-white/40">
          The refusal is the product. A plan that cannot fit arrives as an error with the
          arithmetic attached, before any core runs — silently trimming it would return a
          small delta, which is indistinguishable from a plan that merely worked badly.
        </footer>
      </motion.blockquote>
    </section>
  );
}
