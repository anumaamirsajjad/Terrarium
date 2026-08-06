/**
 * The React wrapper around `speech.ts`.
 *
 * Deliberately thin: it owns a recogniser instance, a listening flag and an error string,
 * and hands the transcript back rather than acting on it. **The transcript is never sent
 * straight to the API** — it lands in the text box, where it can be corrected before
 * anything runs. That matters most in Urdu, where the recogniser is the weak link.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  DEFAULT_LANGUAGE,
  type LanguageTag,
  type SpeechRecognitionLike,
  describeError,
  recognitionConstructor,
  transcriptOf,
} from "./speech";

export interface UseSpeech {
  /** False in browsers without the Web Speech API — render nothing rather than a dud button. */
  supported: boolean;
  listening: boolean;
  error: string | null;
  language: LanguageTag;
  setLanguage: (language: LanguageTag) => void;
  start: () => void;
  stop: () => void;
}

export function useSpeech(onTranscript: (text: string) => void): UseSpeech {
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [language, setLanguage] = useState<LanguageTag>(DEFAULT_LANGUAGE);
  const recognition = useRef<SpeechRecognitionLike | null>(null);

  const Constructor = recognitionConstructor();
  const supported = Constructor !== null;

  // Stop listening if the panel goes away mid-utterance: the recogniser holds the
  // microphone open and does not care that React unmounted.
  useEffect(() => {
    return () => recognition.current?.stop();
  }, []);

  const start = useCallback(() => {
    if (!Constructor) return;

    const instance = new Constructor();
    instance.lang = language;
    instance.continuous = false;
    instance.interimResults = false;

    instance.onresult = (event) => {
      const text = transcriptOf(event);
      if (text) onTranscript(text);
    };
    instance.onerror = (event) => {
      setError(describeError(event.error));
      setListening(false);
    };
    instance.onend = () => setListening(false);

    recognition.current = instance;
    setError(null);
    setListening(true);
    instance.start();
  }, [Constructor, language, onTranscript]);

  const stop = useCallback(() => {
    recognition.current?.stop();
    setListening(false);
  }, []);

  return { supported, listening, error, language, setLanguage, start, stop };
}
