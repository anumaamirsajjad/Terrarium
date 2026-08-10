/**
 * The hero is the product, performed once.
 *
 * Scroll the first screen and the canal plants itself: the extruded raster behind the
 * type cools along the corridor, on the same ramp the map uses. No copy explains it and
 * no button asks for it — by the time the headline has left the screen a visitor has
 * already watched the one thing Terrarium does.
 */

import { ArrowRight } from "lucide-react";
import {
  motion,
  useMotionValue,
  useReducedMotion,
  useScroll,
  useSpring,
  useTransform,
} from "motion/react";
import { Suspense, lazy, useEffect, useRef, useState } from "react";

import { SETTLE, SNAP } from "../../motion/springs";
import { Eyebrow } from "./primitives";

// three.js is most of this page's weight and the headline should paint without it.
const LahoreCity = lazy(() => import("./LahoreCity"));

/** The tile, from config.py. Quoted rather than approximated. */
const BBOX = "74.2533, 31.4305 → 74.4641, 31.6103";

/**
 * How long the camera falls before the route changes, milliseconds.
 *
 * Long enough that the descent is a shot rather than a stutter, short enough that a
 * second press feels ignored rather than broken. The dashboard chunk starts downloading
 * on the same click, so most of this is time the lazy import needed anyway.
 */
const FLIGHT_MS = 1400;

function MagneticCTA({ onEnter }: { onEnter: () => void }) {
  const ref = useRef<HTMLAnchorElement>(null);
  const x = useSpring(useMotionValue(0), SNAP);
  const y = useSpring(useMotionValue(0), SNAP);

  return (
    <motion.a
      ref={ref}
      href="#/app"
      onClick={(event) => {
        // The href stays real — middle-click, ctrl-click and a keyboard Enter with
        // JavaScript broken all still reach the dashboard. This only intercepts the
        // plain left-click, which is the one that can afford to watch a camera move.
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
        event.preventDefault();
        onEnter();
      }}
      style={{ x, y }}
      onPointerMove={(event) => {
        const box = ref.current?.getBoundingClientRect();
        if (!box) return;
        // 0.35, not 1.0. At full strength the button chases the cursor and becomes
        // genuinely hard to click — the pull should be felt, not obeyed.
        x.set((event.clientX - (box.left + box.width / 2)) * 0.35);
        y.set((event.clientY - (box.top + box.height / 2)) * 0.35);
      }}
      onPointerLeave={() => {
        x.set(0);
        y.set(0);
      }}
      whileTap={{ scale: 0.97 }}
      className="group border-shoal/30 bg-shoal/5 hover:border-shoal/70 relative inline-flex
                 shrink-0 items-center gap-3 overflow-hidden rounded-full border px-8 py-4
                 font-mono text-xs tracking-[0.12em] text-white uppercase backdrop-blur-xl
                 transition-colors"
    >
      <span
        aria-hidden
        className="via-shoal/30 absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent
                   to-transparent transition-transform duration-700 ease-out
                   group-hover:translate-x-full"
      />
      <span className="relative">Enter the sandbox</span>
      <ArrowRight className="relative size-3.5 transition-transform group-hover:translate-x-1" />
    </motion.a>
  );
}

export default function Hero() {
  const reduced = useReducedMotion();
  const section = useRef<HTMLElement>(null);
  const [flying, setFlying] = useState(false);
  // Starts true: the hero is what paints first. Once it leaves the viewport the 3D canvas
  // drops to `frameloop="demand"`, so the sticky-scroll sections below it stop competing
  // with a full render nobody can see. A generous rootMargin means the loop resumes just
  // before the hero is back in frame, not after — no visible cold start on scroll-up.
  const [inView, setInView] = useState(true);
  useEffect(() => {
    const node = section.current;
    if (!node) return;
    const observer = new IntersectionObserver(([entry]) => setInView(!!entry?.isIntersecting), {
      rootMargin: "20% 0px",
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  // Reduced motion skips the flight entirely rather than shortening it — the whole point
  // of the gesture is the movement, so there is nothing left to show at a shorter one.
  useEffect(() => {
    if (!flying) return;
    if (reduced) {
      window.location.hash = "#/app";
      return;
    }
    const id = setTimeout(() => {
      window.location.hash = "#/app";
    }, FLIGHT_MS);
    return () => clearTimeout(id);
  }, [flying, reduced]);

  const { scrollYProgress } = useScroll({
    target: section,
    offset: ["start start", "end start"],
  });

  // The intervention lands over the first two thirds of the scroll, then holds. Springing
  // it stops a trackpad flick from snapping the whole city to planted in one frame.
  const plant = useSpring(useTransform(scrollYProgress, [0.05, 0.7], [0, 1]), {
    stiffness: 60,
    damping: 22,
  });

  // The type lifts and fades as the city takes over.
  const typeY = useTransform(scrollYProgress, [0, 1], ["0%", "-38%"]);
  const typeOpacity = useTransform(scrollYProgress, [0, 0.62], [1, 0]);

  return (
    <section ref={section} className="relative h-screen overflow-hidden">
      <div className="absolute inset-0" aria-hidden>
        {/* Stands in until the canvas mounts, and permanently on a machine with no WebGL.
            The page must never open on a black rectangle. */}
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_50%_72%,#0c2c5c_0%,#05070a_58%)]" />
        <Suspense fallback={null}>
          <LahoreCity plant={plant} animate={!reduced} flying={flying} inView={inView} />
        </Suspense>
        {/* Three scrims. The vertical one lifts the headline off the city; the corner one
            darkens behind the eyebrow, which sat unreadable on a block of red roofs; the
            top one carries the attribution line, which on a portrait frame no longer has a
            band of empty sky to sit in — the camera stands higher there so the city fills
            the frame, and the licence text landed on lit rooftops. */}
        <div className="from-void via-void/65 absolute inset-x-0 bottom-0 h-2/3 bg-gradient-to-t to-transparent" />
        <div className="from-void/75 absolute inset-0 bg-gradient-to-tr via-transparent to-transparent" />
        <div className="from-void/80 absolute inset-x-0 top-0 h-28 bg-gradient-to-b to-transparent" />
      </div>

      {/* ODbL requires attribution wherever the geometry is shown. This is a licence
          condition, not a courtesy — it stays. */}
      <p className="absolute top-5 right-5 left-5 z-10 text-right font-mono text-[0.55rem]
                    tracking-wider text-white/30 sm:top-6 sm:right-8 sm:left-auto sm:text-[0.6rem]">
        street geometry © OpenStreetMap contributors
      </p>

      {/* The flight's own fade, wrapping the scroll's. Two nested opacities multiply in
          CSS for free, which is the cheap way to have the type answer to both the scroll
          position and the CTA without either one fighting the other for the property. */}
      <motion.div
        animate={{ opacity: flying ? 0 : 1 }}
        transition={{ duration: 0.45, ease: "easeIn" }}
        className="h-full"
      >
        <motion.div
          style={{ y: typeY, opacity: typeOpacity }}
          className="relative flex h-full flex-col justify-end"
        >
        <div className="mx-auto w-full max-w-[92rem] px-5 pb-14 sm:px-8 sm:pb-16">
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.3, duration: 1 }}
            className="mb-6 flex flex-wrap items-center gap-x-6 gap-y-1 sm:mb-8 sm:gap-y-2"
          >
            <Eyebrow>Lahore · EPSG:32643</Eyebrow>
            {/* /45, not /25. On a portrait frame the camera stands higher and the city
                fills the shot, so this line lands on lit rooftops rather than on the band
                of dark sky it used to have behind it. */}
            <span className="font-mono text-[0.65rem] text-white/45 sm:text-[0.7rem]">{BBOX}</span>
          </motion.div>

          <motion.h1
            initial={{ opacity: 0, y: 48 }}
            animate={{ opacity: 1, y: 0 }}
            transition={SETTLE}
            className="font-mono text-[clamp(3rem,13vw,11.5rem)] leading-[0.82] font-light
                       tracking-[-0.06em] text-white"
          >
            Terrarium
          </motion.h1>

          <motion.div
            initial={{ opacity: 0, y: 28 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ ...SETTLE, delay: 0.14 }}
            className="mt-7 flex flex-col gap-6 border-t border-white/10 pt-6 sm:mt-10 sm:gap-8
                       sm:pt-8 md:flex-row md:items-start md:justify-between"
          >
            <p className="max-w-lg text-base leading-relaxed text-white/60 sm:text-lg">
              A neighbourhood-scale digital twin for climate intervention. Draw a plan on a
              real street; get the modelled change in surface temperature, in the air, and
              in who receives it.
            </p>
            <MagneticCTA onEnter={() => setFlying(true)} />
          </motion.div>
        </div>
        </motion.div>

        {/* What the scroll is about to do. Stated plainly, because an unexplained
            animation is a thing to decode rather than a thing to watch. Shares the type's
            own fade rather than carrying a second one, so the hero clears as a single
            object — and sits inside the flight fade for the same reason, since an
            instruction to scroll is the one line that must not survive into the dive. */}
        <motion.p
          style={{ opacity: typeOpacity }}
          className="pointer-events-none absolute inset-x-0 bottom-5 text-center font-mono
                     text-[0.65rem] tracking-[0.2em] text-white/25 uppercase"
        >
          Scroll to plant the canal
        </motion.p>
      </motion.div>

      {/* The landing on the far side of the dive. Held at nothing for most of the flight
          so the descent is watchable, then closed to the page's own black over the last
          third — which is what the dashboard mounts behind, so the route change is a
          cut in the dark rather than a flash of two layouts. */}
      {flying && (
        <motion.div
          aria-hidden
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: FLIGHT_MS / 1000, ease: [0.7, 0, 1, 1] }}
          className="bg-void pointer-events-none absolute inset-0 z-20"
        />
      )}
    </section>
  );
}
