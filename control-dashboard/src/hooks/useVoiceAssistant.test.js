import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

vi.mock("../api/client.js", () => ({
  apiPostJson: vi.fn().mockResolvedValue({ replyText: "ok", action: null }),
}));

vi.mock("../config.js", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    PI_VOICE_ENDPOINT: "https://example.test/api/voice",
  };
});

import { useVoiceAssistant } from "./useVoiceAssistant.js";
import { apiPostJson } from "../api/client.js";

describe("useVoiceAssistant", () => {
  beforeEach(() => {
    vi.mocked(apiPostJson).mockClear();
  });

  it("sendText posts transcript", async () => {
    const { result } = renderHook(() => useVoiceAssistant({ onAction: vi.fn() }));
    await act(async () => {
      await result.current.sendText("hello");
    });
    expect(apiPostJson).toHaveBeenCalledWith(
      "https://example.test/api/voice",
      { transcript: "hello" },
      expect.any(Object),
    );
    expect(result.current.lastTranscript).toBe("hello");
  });
});
