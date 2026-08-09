/**
 * Reading an equity distribution.
 *
 * The interpretation lives here rather than in the component so it can be tested without
 * a DOM — the same split the rest of this frontend uses (`raster/decode`, `units`).
 *
 * The verdict order matters and is not arbitrary. Cooling that lands on empty ground is
 * reported *before* concentration, because it is the more damaging failure and the one a
 * ΔLST headline hides most completely: a plan can look like the best on the map, be
 * perfectly even across the deciles it does reach, and still deliver a quarter of its
 * effect to a riverbank nobody lives on.
 */

import type { Equity } from "../api/client";

/** An even split gives every decile a tenth. */
export const EVEN_SHARE = 0.1;
/** …so three deciles evenly served hold 0.30 between them. */
export const EVEN_TOP_THREE = 0.3;
/** Above this share of cooling landing on empty land, that is the headline. */
export const WASTED_THRESHOLD = 0.2;

export type EquityTone = "even" | "skewed" | "wasted" | "no-net-effect";

export interface EquityVerdict {
  tone: EquityTone;
  headline: string;
  detail: string;
}

/** Share held by the most densely populated decile — the people most exposed to heat. */
export function densestShare(equity: Equity): number {
  const last = equity.deciles.at(-1);
  return last ? last.share : 0;
}

export function equityVerdict(equity: Equity): EquityVerdict {
  const wasted = equity.uninhabited_fraction;
  const densest = densestShare(equity);

  // Before anything else: shares divide by the net benefit, so a plan that cools and
  // warms in equal measure has no denominator worth dividing by. Saying "no net effect"
  // is the honest answer; drawing bars off a vanishing total is not.
  if (!equity.shares_reliable) {
    return {
      tone: "no-net-effect",
      headline: "This plan has no net effect to share out",
      detail:
        "It cools and warms in near-equal measure, so there is no meaningful total to " +
        "distribute across the population. The per-decile shares are not shown because " +
        "they would divide by a number close to zero.",
    };
  }

  if (wasted >= WASTED_THRESHOLD) {
    return {
      tone: "wasted",
      headline: `${(wasted * 100).toFixed(0)}% of this cooling reaches nobody`,
      detail:
        "It lands on cells with no residents. A plan can cool more overall and still " +
        "help fewer people — compare plans on this number, not on the cooling map alone.",
    };
  }

  if (equity.concentrated) {
    return {
      tone: "skewed",
      headline: `${(equity.top_three_share * 100).toFixed(0)}% goes to three deciles`,
      detail:
        `An even split would give those three ${(EVEN_TOP_THREE * 100).toFixed(0)}%. ` +
        `The most crowded decile receives ${(densest * 100).toFixed(1)}%.`,
    };
  }

  return {
    tone: "even",
    headline: "Benefit is spread across the population",
    detail:
      `The three best-served deciles hold ${(equity.top_three_share * 100).toFixed(0)}%, ` +
      `against ${(EVEN_TOP_THREE * 100).toFixed(0)}% for a perfectly even split.`,
  };
}

export interface BarGeometry {
  /** Width as a percentage of the widest bar in the chart. */
  widthPct: number;
  /** A decile that was made hotter. Drawn the other way, never clipped to zero. */
  warming: boolean;
}

/**
 * Scale one decile's share against the largest magnitude in the set.
 *
 * Normalised to the biggest bar rather than to a fixed 100%: an even distribution puts
 * every bar at 10%, which would render as ten slivers and show nothing. Magnitude is used
 * so a warming decile gets a visible bar too.
 */
export function barGeometry(share: number, shares: readonly number[]): BarGeometry {
  const widest = Math.max(...shares.map(Math.abs), Number.EPSILON);
  return {
    widthPct: (Math.abs(share) / widest) * 100,
    warming: share < 0,
  };
}

/** Where the "even share" reference line sits, on the same scale as the bars. */
export function evenMarkerPct(shares: readonly number[]): number {
  const widest = Math.max(...shares.map(Math.abs), Number.EPSILON);
  return Math.min((EVEN_SHARE / widest) * 100, 100);
}
