/**
 * The frosted shell every result panel floats in.
 *
 * One component rather than six classes repeated at each call site, and one entrance rather
 * than four that nearly match. `AnimatePresence` in `App` gives it the exit, so a cleared
 * scenario animates out instead of the whole right-hand column vanishing between frames.
 *
 * Not draggable, deliberately. `drag` plus `dragConstraints` is two props, but a panel
 * dragged over a map needs collision, persistence and a reset affordance before it stops
 * being annoying, and fixed positions with a spring get most of the feel for none of it.
 */

import { motion } from "motion/react";
import type { ReactNode } from "react";

import { GLIDE } from "../motion/springs";

export interface FloatingPanelProps {
  children: ReactNode;
  className?: string;
}

export default function FloatingPanel({ children, className = "" }: FloatingPanelProps) {
  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: 20, filter: "blur(6px)" }}
      animate={{ opacity: 1, x: 0, filter: "blur(0px)" }}
      exit={{ opacity: 0, x: 20, filter: "blur(6px)" }}
      transition={GLIDE}
      className={`glass noise p-4 ${className}`}
    >
      {children}
    </motion.div>
  );
}
