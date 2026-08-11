/**
 * Who actually receives the cooling.
 *
 * This is the panel that critiques the user's own plan, so it is written to be capable of
 * saying something unwelcome. Two rules follow from that:
 *
 * 1. **Deciles hold a tenth of the people, not a tenth of the map.** So an even split is
 *    10% everywhere, the reference line means something, and no arithmetic is needed to
 *    see a skew.
 * 2. **A warmed decile is drawn, not clipped.** A plan that cools the sparse edge and
 *    warms the crowded middle must not be able to render as a clean row of bars.
 */

import { motion } from "motion/react";

import type { Equity } from "../api/client";
import { SETTLE } from "../motion/springs";
import { barGeometry, densestShare, equityVerdict, evenMarkerPct } from "./equity";

export interface EquityPanelProps {
  equity: Equity | null;
}

export default function EquityPanel({ equity }: EquityPanelProps) {
  // Null means the cube carried no population layer — which is not the same as "nobody
  // benefits", so it must not render as a row of zeroes.
  if (!equity) return null;

  const shares = equity.deciles.map((d) => d.share);
  const verdict = equityVerdict(equity);
  const markerPct = evenMarkerPct(shares);

  return (
    <section className="equity" aria-labelledby="equity-heading">
      <h2 id="equity-heading">Who gets the cooling</h2>

      {/* The pulse is driven off the verdict's own tone rather than re-deriving the 20 %
          boundary here. `equityVerdict` already owns that threshold, and a second copy of
          it in the view is a copy that will disagree. */}
      <motion.p
        animate={verdict.tone === "wasted" ? { opacity: [1, 0.55, 1] } : { opacity: 1 }}
        transition={
          verdict.tone === "wasted" ? { duration: 2.2, repeat: Infinity } : { duration: 0.2 }
        }
        className={`equity__verdict equity__verdict--${verdict.tone}`}
      >
        <strong>{verdict.headline}</strong> {verdict.detail}
      </motion.p>

      {equity.shares_reliable && (
      <div className="equity__chart" role="img" aria-label={verdict.headline}>
        <div className="equity__marker" style={{ left: `${markerPct}%` }}>
          <span>even share</span>
        </div>

        {equity.deciles.map((decile) => {
          const { widthPct, warming } = barGeometry(decile.share, shares);
          return (
            <div className="equity__row" key={decile.decile}>
              <span className="equity__label">{decile.decile}</span>
              <span className="equity__track">
                {/* `equity__bar` and `equity__bar--warming` are load-bearing names, not
                    decoration: the panel's test counts rows and asserts that a warmed
                    decile is drawn rather than clipped. They survive the animation. */}
                <motion.span
                  className={`equity__bar${warming ? " equity__bar--warming" : ""}`}
                  initial={{ width: 0 }}
                  animate={{ width: `${widthPct}%` }}
                  transition={{ ...SETTLE, delay: decile.decile * 0.045 }}
                />
              </span>
              <span className="equity__share">{(decile.share * 100).toFixed(1)}%</span>
            </div>
          );
        })}
      </div>
      )}

      {equity.shares_reliable && (
      <p className="equity__axis">
        <span className="muted">
          1 = least densely populated &middot; 10 = most densely populated &middot; each
          decile holds {(equity.population_covered / 10 / 1000).toFixed(0)}k residents
        </span>
      </p>
      )}

      <dl className="equity__stats">
        <div>
          <dt>Reaching nobody</dt>
          <dd>
            {(equity.uninhabited_fraction * 100).toFixed(1)}%
            <span className="muted"> of the cooling, by area</span>
          </dd>
        </div>
        <div>
          <dt>Most crowded decile</dt>
          <dd>
            {(densestShare(equity) * 100).toFixed(1)}%
            <span className="muted"> of the benefit</span>
          </dd>
        </div>
      </dl>
    </section>
  );
}
