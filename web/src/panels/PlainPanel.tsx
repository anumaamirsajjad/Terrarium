/**
 * The result in words a non-specialist reads, and the only panel open by default.
 *
 * Everything on this screen used to arrive at once: six statistics, a ratio against a
 * linear expectation, a paragraph about winter contrast noise, ten uncertainties. All of
 * it true, and all of it addressed to somebody who will be asked to defend the number
 * rather than somebody deciding whether the plan is worth doing. This panel is for the
 * second reader; `ResultPanel`, `EquityPanel` and `AirPanel` are still one click away for
 * the first.
 *
 * **The text is not written here.** Every sentence comes from `brief.plain` on the server,
 * where it is generated from templates and — when a key is configured — reworded by the
 * narrator under a guard that rejects any figure the template did not contain. So this
 * component cannot introduce a number, and cannot drop a caveat the API attached.
 *
 * The one number rendered large is `expected_cooling_c`, which is **already divided by the
 * hindcast factor**. The raw modelled figure is deliberately not shown at this size: it is
 * the upper bound, it lives in the technical panels beside its correction, and a reader
 * who sees only one of the two should see the conservative one.
 */

import type { Brief, PlainSummary } from "../api/client";

export interface PlainPanelProps {
  brief: Brief;
}

/**
 * The verdict as a word and a colour. Deliberately not a green-to-red scale: "marginal"
 * is not a failure and "large" is not a success — they describe the size of a change, and
 * a reader who sees red for a small honest result learns to read the badge as a score.
 * Amber is reserved for the two that mean "this number cannot be sized", which is the one
 * state worth interrupting someone over.
 */
const VERDICT: Record<PlainSummary["verdict"], { label: string; className: string } | null> = {
  large: { label: "big change", className: "bg-emerald-400/15 text-emerald-300" },
  moderate: { label: "real but modest", className: "bg-emerald-400/10 text-emerald-200/90" },
  small: { label: "small change", className: "bg-sky-400/10 text-sky-200/90" },
  marginal: { label: "barely anything", className: "bg-white/8 text-white/55" },
  unrated: { label: "size not verified", className: "bg-amber-400/10 text-amber-200/90" },
  // The headline already says nothing happened; a badge saying so twice is noise.
  none: null,
};

export default function PlainPanel({ brief }: PlainPanelProps) {
  const { plain } = brief;
  const cooling = Math.abs(brief.expected_cooling_c);
  // Below a hundredth of a degree the rounded figure reads "0.00", which looks like a bug
  // rather than a small result. The headline sentence already says what happened.
  const measurable = cooling >= 0.005;
  // An air-only plan (a restriction with no planting) never has a measurable temperature
  // change, so it used to fall through to no headline number at all — the paragraph below
  // was the only place the improvement showed up. This gives it the same prominence a
  // cooling result gets, using the same structured field `expected_cooling_c` does rather
  // than parsing a number back out of `plain.headline`.
  const pm25 = brief.expected_pm25_delta !== null ? Math.abs(brief.expected_pm25_delta) : null;
  const pm25Improved = !measurable && pm25 !== null && pm25 >= 0.005;
  const verdict = VERDICT[plain.verdict];

  return (
    <section aria-labelledby="plain-heading">
      <h2 id="plain-heading" className="sr-only">
        What this plan would do
      </h2>

      {measurable && (
        <div className="flex items-baseline gap-2">
          <span className="text-4xl font-semibold tracking-tight text-emerald-300">
            {cooling.toFixed(2)} °C
          </span>
          <span className="text-xs text-white/50">cooler ground, on average</span>
        </div>
      )}

      {pm25Improved && (
        <div className="flex items-baseline gap-2">
          <span className="text-4xl font-semibold tracking-tight text-sky-300">
            {pm25!.toFixed(2)} {brief.pm25_units}
          </span>
          <span className="text-xs text-white/50">cleaner air, on average</span>
        </div>
      )}

      {verdict && (
        <span
          className={`mt-2 inline-block rounded-full px-2 py-0.5 text-[0.62rem] font-medium uppercase tracking-wide ${verdict.className}`}
        >
          {verdict.label}
        </span>
      )}

      <div>
        <p className="mt-3 text-sm leading-relaxed text-white/85">{plain.headline}</p>

        <ul className="mt-4 space-y-2">
          {plain.points.map((point) => (
            <li key={point} className="flex gap-2 text-xs leading-relaxed text-white/65">
              <span
                aria-hidden="true"
                className="mt-1.5 size-1 shrink-0 rounded-full bg-white/30"
              />
              <span>{point}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
