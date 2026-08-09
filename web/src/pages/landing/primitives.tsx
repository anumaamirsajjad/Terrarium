/**
 * The page's shared motion vocabulary.
 *
 * Four primitives, used everywhere, so that a reveal on one section is the same reveal on
 * the next. Scattering bespoke transitions across nine components is how a page ends up
 * with eleven easings nobody can name.
 */

import { animate, motion, useInView, useReducedMotion } from "motion/react";
import { useEffect, useRef, useState } from "react";

import { GLIDE, SETTLE } from "../../motion/springs";
import type { MarginNote } from "./marginNote";

/**
 * A line of type wiped up from behind its own baseline.
 *
 * Word by word, not letter by letter. Latin text survives either, but the same component
 * gets pointed at Urdu elsewhere on this page, and splitting a cursive script into
 * per-character elements breaks the joins between letters — the sentence renders as a row
 * of disconnected forms. Words are the smallest safe unit.
 */
export function Reveal({
  text,
  className = "",
  delay = 0,
  as: Tag = "p",
}: {
  text: string;
  className?: string;
  delay?: number;
  as?: "h1" | "h2" | "h3" | "p";
}) {
  const reduced = useReducedMotion();
  const words = text.split(" ");

  return (
    <Tag className={className}>
      {words.map((word, index) => (
        <span
          key={`${word}-${index}`}
          className="inline-block overflow-hidden align-bottom"
          // The mask is the line box itself. Padding would clip descenders.
          style={{ paddingBottom: "0.12em", marginBottom: "-0.12em" }}
        >
          <motion.span
            className="inline-block"
            initial={reduced ? undefined : { y: "115%" }}
            whileInView={reduced ? undefined : { y: 0 }}
            viewport={{ once: true, amount: 0.6 }}
            transition={{ ...SETTLE, delay: delay + index * 0.035 }}
          >
            {word}
            {index < words.length - 1 ? " " : ""}
          </motion.span>
        </span>
      ))}
    </Tag>
  );
}

/**
 * A figure that counts up once, when it is first seen.
 *
 * Every number this component is pointed at is a measured one, so it counts to the real
 * value and stops — no easing past the target and settling back, which reads as a
 * slot machine and undercuts the figure.
 */
export function Counter({
  to,
  decimals = 0,
  prefix = "",
  suffix = "",
  className = "",
}: {
  to: number;
  decimals?: number;
  prefix?: string;
  suffix?: string;
  className?: string;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const seen = useInView(ref, { once: true, amount: 0.8 });
  const reduced = useReducedMotion();
  const [shown, setShown] = useState(0);

  useEffect(() => {
    if (!seen) return;
    if (reduced) {
      setShown(to);
      return;
    }
    const controls = animate(0, to, {
      duration: 1.4,
      ease: [0.16, 1, 0.3, 1],
      onUpdate: setShown,
    });
    return () => controls.stop();
  }, [seen, to, reduced]);

  return (
    <span ref={ref} className={className}>
      {prefix}
      {shown.toLocaleString(undefined, {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      })}
      {suffix}
    </span>
  );
}

/**
 * The margin rail's own note, restated in flow.
 *
 * `MarginRail` is `hidden ... xl:block` — a caveat crushed into a phone's gutter is a
 * caveat nobody reads, so below `xl` it never renders at all. This is the inline
 * replacement: closed by default so it doesn't compete with the section's own copy, one
 * per section, reading the same `note` object the rail was handed so the two can never
 * drift apart.
 */
export function InlineCaveat({ note }: { note: MarginNote }) {
  return (
    <details className="mt-8 border-t border-white/10 pt-5 xl:hidden">
      <summary className="cursor-pointer list-none font-mono text-[0.6rem] tracking-[0.2em] text-white/30 uppercase">
        What this does not prove
      </summary>
      <p className="text-shoal/90 mt-3 font-mono text-[0.7rem] leading-relaxed">
        {note.subject}
      </p>
      <p className="mt-2 text-[0.72rem] leading-relaxed text-white/45">{note.body}</p>
    </details>
  );
}

/** A hairline that draws itself left to right as its section arrives. */
export function Rule({ className = "", delay = 0 }: { className?: string; delay?: number }) {
  return (
    <motion.div
      initial={{ scaleX: 0 }}
      whileInView={{ scaleX: 1 }}
      viewport={{ once: true }}
      transition={{ ...GLIDE, delay }}
      style={{ transformOrigin: "left" }}
      className={`hairline ${className}`}
    />
  );
}

/**
 * An eyebrow that says where in the codebase the thing being described lives.
 *
 * A structural label rather than a decorative one: `cores/thermal` is true, checkable, and
 * tells a reader something a sequence number would not. The three simulators are parallel,
 * not ordered, so numbering them 01/02/03 would assert a sequence that does not exist.
 */
export function Eyebrow({ children }: { children: React.ReactNode }) {
  return (
    <span className="font-mono text-[0.7rem] tracking-[0.22em] text-shoal uppercase">
      {children}
    </span>
  );
}
