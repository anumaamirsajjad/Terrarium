/**
 * Two entry points, one bundle split.
 *
 * The landing page pulls in three.js and R3F; the dashboard pulls in MapLibre and
 * deck.gl. Neither needs the other's several hundred kilobytes, and `React.lazy` on a
 * hash check buys the split for nine lines — where a router would be a dependency, a
 * provider and a config for two routes that never take a parameter. Add one at the third
 * route.
 */

import { Suspense, lazy, useEffect, useState } from "react";

const Landing = lazy(() => import("./pages/Landing"));
const App = lazy(() => import("./App"));

/** The dashboard. Anything else — including no hash at all — is the landing page. */
const APP_ROUTE = "#/app";

function Booting() {
  return (
    <div className="bg-void grid h-screen place-items-center">
      <span className="font-mono text-xs tracking-widest text-white/30 uppercase">
        Terrarium
      </span>
    </div>
  );
}

export default function Root() {
  const [hash, setHash] = useState(() => window.location.hash);

  useEffect(() => {
    const onHash = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // The dashboard mounts a map and expects to own the viewport; the landing page scrolls.
  // Setting this here rather than in either page keeps the two from fighting over it.
  useEffect(() => {
    document.body.style.overflow = hash === APP_ROUTE ? "hidden" : "";
  }, [hash]);

  return <Suspense fallback={<Booting />}>{hash === APP_ROUTE ? <App /> : <Landing />}</Suspense>;
}
