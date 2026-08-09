/**
 * One motion vocabulary, so the whole app feels like a single object.
 *
 * Three springs and nothing else. A per-component transition is how an interface ends up
 * with eleven slightly different easings that nobody can name — the reason to centralise
 * this is consistency, not reuse.
 *
 * Durations are emergent: a spring is described by how heavy the thing is, not by how
 * long it should take, which is what keeps an interrupted animation from snapping.
 */

import type { Transition, Variants } from "motion/react";

/** Buttons, toggles, cursor-following. Light, near-instant, barely overshoots. */
export const SNAP: Transition = { type: "spring", stiffness: 520, damping: 34 };

/** Panels and modals entering or leaving. Has some weight to it. */
export const GLIDE: Transition = { type: "spring", stiffness: 210, damping: 26 };

/** Bars growing, large reveals. Slow enough that the eye can follow the value. */
export const SETTLE: Transition = { type: "spring", stiffness: 90, damping: 20 };

/** The scroll-reveal: up and in. Paired with STAGGER on the parent. */
export const RISE: Variants = {
  hidden: { opacity: 0, y: 24 },
  show: { opacity: 1, y: 0, transition: SETTLE },
};

export const STAGGER: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.07 } },
};

/*
 * For the reduced-motion gate, use `useReducedMotion` from motion/react rather than a
 * helper here. The CSS rule in index.css covers anything declarative, but it cannot stop
 * a WebGL render loop or a requestAnimationFrame canvas — those have to ask, and the hook
 * already tracks changes to the preference.
 */
