/**
 * The pure half of voice capture.
 *
 * The recogniser itself is a browser object that cannot be exercised in vitest, so what is
 * tested is everything around it: whether support is detected honestly, whether a
 * transcript is assembled correctly, and whether a failure code becomes a sentence a user
 * can act on rather than "error: not-allowed".
 */

import { describe, expect, it } from "vitest";

import {
  DEFAULT_LANGUAGE,
  LANGUAGES,
  describeError,
  recognitionConstructor,
  transcriptOf,
} from "./speech";

class FakeRecognition {}

describe("recognitionConstructor", () => {
  it("returns null where the API does not exist", () => {
    // Firefox. Rendering a microphone button that silently does nothing is worse than
    // rendering none.
    expect(recognitionConstructor({})).toBeNull();
  });

  it("prefers the unprefixed constructor and accepts the webkit one", () => {
    expect(recognitionConstructor({ SpeechRecognition: FakeRecognition as never })).toBe(
      FakeRecognition,
    );
    expect(
      recognitionConstructor({ webkitSpeechRecognition: FakeRecognition as never }),
    ).toBe(FakeRecognition);
  });

  it("is safe where there is no window at all", () => {
    expect(recognitionConstructor(undefined)).toBeNull();
  });
});

describe("transcriptOf", () => {
  it("joins every result segment", () => {
    const event = {
      results: [[{ transcript: "plant 5000 trees" }], [{ transcript: " in winter" }]],
    };
    expect(transcriptOf(event)).toBe("plant 5000 trees in winter");
  });

  it("returns an empty string when nothing was heard", () => {
    expect(transcriptOf({ results: [] })).toBe("");
  });
});

describe("describeError", () => {
  it("explains a refused microphone and offers the way round it", () => {
    const message = describeError("not-allowed");
    expect(message).toContain("Microphone access was refused");
    expect(message).toContain("type the plan");
  });

  it("says typing works offline when the speech service is unreachable", () => {
    // Worth stating: the recogniser is the only part of this product that needs a network
    // at all once the cube is loaded.
    expect(describeError("network")).toContain("offline");
  });

  it("keeps an unknown code visible rather than swallowing it", () => {
    expect(describeError("weird-new-code")).toContain("weird-new-code");
    expect(describeError(undefined)).toContain("unknown");
  });
});

describe("languages", () => {
  it("offers exactly the two the rule parser can read", () => {
    // Adding a language here without adding it to `dsl/planner.py` would ship a
    // microphone that produces a 422.
    expect(LANGUAGES.map((l) => l.tag)).toEqual(["en-US", "ur-PK"]);
    expect(DEFAULT_LANGUAGE).toBe("en-US");
  });
});
