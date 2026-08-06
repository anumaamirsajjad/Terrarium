/**
 * The result in sentences — the council brief, in its browser form.
 *
 * Every word here was written on the server by a deterministic template, and this panel
 * renders it without editing. That is deliberate in both directions: the API cannot ship a
 * figure this panel silently drops, and the panel cannot soften a caveat the API decided
 * to attach.
 *
 * **The uncertainties are not collapsed behind a toggle.** They carry the hindcast
 * correction, the window, and the surface-versus-air distinction, and a reader who takes
 * the headline without them has the wrong number.
 */

import type { Brief } from "../api/client";

export interface BriefPanelProps {
  brief: Brief;
}

export default function BriefPanel({ brief }: BriefPanelProps) {
  return (
    <section className="brief" aria-labelledby="brief-heading">
      <h2 id="brief-heading">The brief</h2>

      <p className="brief__headline">{brief.headline}</p>

      <ul className="brief__findings">
        {brief.findings.map((finding) => (
          <li key={finding}>{finding}</li>
        ))}
      </ul>

      <h3 className="brief__subhead">
        What this does not prove
        <span className={`brief__confidence brief__confidence--${brief.confidence}`}>
          {brief.confidence} confidence
        </span>
      </h3>

      <ul className="brief__uncertainties">
        {brief.uncertainties.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </section>
  );
}
