/**
 * Voice capture through the browser's own speech recogniser.
 *
 * **Web Speech API, not a hosted transcription service.** LiveKit and every managed STT
 * meter minutes and want a card; `webkitSpeechRecognition` is already in the browser, is
 * free, needs no key, and keeps the zero-budget claim intact. The cost is that support is
 * uneven — Chrome and Edge have it, Firefox does not — so this module's first job is
 * detecting that honestly rather than rendering a microphone that does nothing.
 *
 * The pure parts live here so they can be tested without a DOM; `useSpeech` is the thin
 * React wrapper around them.
 */

/** The two languages the DSL's rule parser can actually read (see `dsl/planner.py`). */
export const LANGUAGES = [
  { tag: "en-US", label: "English" },
  { tag: "ur-PK", label: "اردو" },
] as const;

export type LanguageTag = (typeof LANGUAGES)[number]["tag"];

export const DEFAULT_LANGUAGE: LanguageTag = "en-US";

/**
 * Urdu recognition is materially weaker than English in every engine, including this one.
 * Saying so next to the control is the honest version of shipping the feature: the parser
 * understands Urdu, the *microphone* is the weak link, and a user who is misheard should
 * know which half failed.
 */
export const URDU_CAVEAT =
  "Urdu recognition is weaker than English in every browser engine. If it mishears you, " +
  "the transcript is editable before it is sent — and typing Urdu works exactly as well.";

/** A minimal structural type for the vendor-prefixed API. */
export interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start(): void;
  stop(): void;
  onresult: ((event: SpeechRecognitionResultLike) => void) | null;
  onerror: ((event: { error?: string }) => void) | null;
  onend: (() => void) | null;
}

export interface SpeechRecognitionResultLike {
  results: ArrayLike<ArrayLike<{ transcript: string }>>;
}

type RecognitionConstructor = new () => SpeechRecognitionLike;

interface SpeechCapableWindow {
  SpeechRecognition?: RecognitionConstructor;
  webkitSpeechRecognition?: RecognitionConstructor;
}

/**
 * The constructor this browser offers, or null.
 *
 * Returned rather than a boolean so the caller cannot check support and then construct a
 * different object — the check and the thing checked are the same value.
 */
export function recognitionConstructor(
  scope: SpeechCapableWindow | undefined = typeof window === "undefined"
    ? undefined
    : (window as unknown as SpeechCapableWindow),
): RecognitionConstructor | null {
  if (!scope) return null;
  return scope.SpeechRecognition ?? scope.webkitSpeechRecognition ?? null;
}

/** Join a recognition event's alternatives into one transcript. */
export function transcriptOf(event: SpeechRecognitionResultLike): string {
  let text = "";
  for (let i = 0; i < event.results.length; i += 1) {
    const alternative = event.results[i][0];
    if (alternative) text += alternative.transcript;
  }
  return text.trim();
}

/** What went wrong, in words a user can act on rather than a Web Speech error code. */
export function describeError(code: string | undefined): string {
  switch (code) {
    case "not-allowed":
    case "service-not-allowed":
      return "Microphone access was refused. Allow it in the browser, or type the plan instead.";
    case "no-speech":
      return "Nothing was heard. Try again, closer to the microphone.";
    case "network":
      return "The browser's speech service could not be reached. Typing works offline.";
    case "aborted":
      return "Listening stopped.";
    default:
      return `Speech recognition failed (${code ?? "unknown"}). Typing the plan works too.`;
  }
}
