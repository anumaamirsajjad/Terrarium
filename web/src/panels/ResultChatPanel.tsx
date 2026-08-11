/**
 * Talk to the result, not to the repository.
 *
 * Replaces "ask the record" (Phase B, withdrawn): that panel answered from this project's
 * own markdown, which is a fine question to ask about the codebase and the wrong one to ask
 * about a specific run. This panel only ever sees the brief the run actually produced —
 * `/simulate/chat` is handed that brief and nothing else, so a follow-up question can be
 * answered ("why did it cool that much?") without the model reaching for its memory of
 * urban climate in general.
 *
 * **Every prior turn goes back with the next question.** The conversation is kept in
 * `App.tsx`, keyed to the current result, and cleared the moment a new `/simulate` result
 * replaces it — a follow-up about a different run answered from the old one would be a
 * wrong answer wearing a right one's shape.
 */

import { useState } from "react";

import type { ChatTurn } from "../api/client";

export interface ResultChatPanelProps {
  messages: ChatTurn[];
  busy: boolean;
  error: string | null;
  onAsk: (question: string) => void;
  /** Prefilled when the user tapped "explain this" on a figure elsewhere in the UI. */
  initialQuestion?: string;
}

export default function ResultChatPanel({
  messages,
  busy,
  error,
  onAsk,
  initialQuestion = "",
}: ResultChatPanelProps) {
  const [question, setQuestion] = useState(initialQuestion);

  return (
    <section className="control chat" aria-labelledby="chat-heading">
      <h2 id="chat-heading">Ask about this result</h2>

      {messages.length > 0 && (
        <ul className="chat__messages" aria-label="Conversation so far">
          {messages.map((turn, index) => (
            <li
              key={index}
              className={`chat__message${turn.role === "user" ? " is-user" : " is-assistant"}`}
            >
              {turn.content}
            </li>
          ))}
        </ul>
      )}

      {error && <p className="error-text">{error}</p>}
      {busy && <p className="hint">Thinking…</p>}

      <form
        className="chat__ask"
        onSubmit={(event) => {
          event.preventDefault();
          if (question.trim()) {
            onAsk(question.trim());
            setQuestion("");
          }
        }}
      >
        <label className="field">
          <span>What do you want to know about this result?</span>
          <input
            type="text"
            value={question}
            placeholder="why did it cool that much?"
            onChange={(event) => setQuestion(event.target.value)}
            disabled={busy}
            dir="auto"
          />
        </label>

        <div className="row">
          <button type="submit" disabled={busy || !question.trim()}>
            {busy ? "Asking…" : "Ask"}
          </button>
        </div>

        <p className="hint">
          Answered from this result's own brief and nothing else. A question this brief
          cannot answer without inventing a number is refused rather than guessed at.
        </p>
      </form>
    </section>
  );
}
