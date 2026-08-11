/**
 * The landing page.
 *
 * Every figure here is checkable against the repo. Where a number would have been
 * flattering and wrong — a bigger seasonal factor, a cooling this tile cannot deliver, a
 * feature that was removed — the smaller true one is used instead. That is the same rule
 * the brief and the panels follow, and it is the only reason a visitor should believe any
 * of the rest of it.
 *
 * The palette is the HEAT ramp's own stops, so the page and the map are one colour system.
 * Display type is Geist Mono, because what this instrument produces is numbers.
 */

import { MotionConfig } from "motion/react";

import Alignment from "./landing/Alignment";
import Architecture from "./landing/Architecture";
import CommandDemo from "./landing/CommandDemo";
import Hero from "./landing/Hero";
import Outro from "./landing/Outro";
import ScrollChrome from "./landing/ScrollChrome";
import Simulators from "./landing/Simulators";

export default function Landing() {
  return (
    // `reducedMotion="user"` makes every transform and layout animation on this page
    // honour the OS preference automatically, leaving opacity alone. The canvases opt out
    // separately, because a render loop is not something this can reach.
    <MotionConfig reducedMotion="user">
      <ScrollChrome />

      {/**
       * `overflow-x-clip`, never `overflow-x-hidden`.
       *
       * `hidden` makes this element a scroll container, and a scroll container between
       * the viewport and a `position: sticky` child breaks the child — it sticks to an
       * ancestor that never scrolls, so it simply scrolls away. That silently emptied
       * the sticky simulator narrative and the architecture rail: three screens of
       * nothing, with no error anywhere. `clip` trims the overflow without creating a
       * scroll container, which is exactly what is wanted here.
       */}
      <main className="bg-void min-h-screen overflow-x-clip text-white antialiased">
        <Hero />
        <div className="raster">
          <Alignment />
          <Simulators />
          <Architecture />
          <CommandDemo />
          <Outro />
        </div>
      </main>
    </MotionConfig>
  );
}
