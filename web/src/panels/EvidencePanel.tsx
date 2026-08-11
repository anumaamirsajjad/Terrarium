/**
 * Ask the project's own record — "why is the cooling divided by 2.5?" — and get the
 * documentation that answers it.
 *
 * The design decision worth stating: **the passages are always shown, under the answer.**
 * Not collapsed behind a "sources" toggle. The whole claim this feature makes is that the
 * answer came out of the repository rather than out of a model's memory, and a reader can
 * only check that if the evidence is on the same screen as the prose.
 *
 * **A failure is a failure now, not a quieter answer.** This panel used to render "the
 * model cited something that does not exist, so here are the passages instead" as a
 * successful response with a flag set, which is how a guard firing came to look like a
 * working feature. The route returns 4xx/5xx for all three failure modes, and `error`
 * below carries the reason — including the anchors that were fabricated.
 */

import { useState } from "react";

import type { Answer } from "../api/client";

export interface EvidencePanelProps {
  answer: Answer | null;
  busy: boolean;
  error: string | null;
  onAsk: (question: string) => void;
  /** Prefilled when the user tapped "explain this" on a figure elsewhere in the UI. */
  initialQuestion?: string;
}

/** The questions this corpus answers unusually well, because it wrote them down itself. */
const SUGGESTIONS = [
  "Why is the cooling divided by 2.5?",
  "Why does the air core beat the null model in winter but not summer?",
  "Why is the LLM optional?",
];

export default function EvidencePanel({
  answer,
  busy,
  error,
  onAsk,
  initialQuestion = "",
}: EvidencePanelProps) {
  const [question, setQuestion] = useState(initialQuestion);

  return (
    <section className="control evidence" aria-labelledby="evidence-heading">
      <h2 id="evidence-heading">Ask the record</h2>

      <form
        className="evidence__ask"
        onSubmit={(event) => {
          event.preventDefault();
          if (question.trim()) onAsk(question.trim());
        }}
      >
        <label className="field">
          <span>What do you want to know?</span>
          <input
            type="text"
            value={question}
            placeholder="why is the cooling divided by 2.5?"
            onChange={(event) => setQuestion(event.target.value)}
            disabled={busy}
            dir="auto"
          />
        </label>

        <div className="row">
          <button type="submit" disabled={busy || !question.trim()}>
            {busy ? "Looking…" : "Ask"}
          </button>
        </div>

        <div className="evidence__suggestions">
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              className="evidence__suggestion"
              disabled={busy}
              onClick={() => {
                setQuestion(suggestion);
                onAsk(suggestion);
              }}
            >
              {suggestion}
            </button>
          ))}
        </div>

        <p className="hint">
          Answered from this project's own documentation and nothing else. Every citation is
          checked against the passages the model was actually shown — one that does not
          resolve rejects the whole answer, and you get told rather than quietly handed the
          sections instead.
        </p>
      </form>

      {error && <p className="error-text">{error}</p>}

      {answer && (
        <div className="evidence__answer">
          <p className="evidence__provenance is-grounded">
            Written by {answer.source}, from the passages below. Every citation in it
            resolves to one of them.
          </p>

          {/* `pre-wrap`: the model writes paragraphs and they are its only structure. */}
          <p className="evidence__body">{answer.answer}</p>

          {answer.passages.length > 0 && (
            <>
              <h3 className="evidence__sources-heading">What this came from</h3>
              <ul className="evidence__sources">
                {answer.passages.map((section) => (
                  <li key={section.anchor}>
                    <code>{section.anchor}</code>
                    <span className="muted"> · {section.heading}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </section>
  );
}
