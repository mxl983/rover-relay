import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useVoiceAssistant } from "./useVoiceAssistant.js";

vi.mock("../api/client.js", () => ({
  apiPostJson: vi.fn().mockResolvedValue({ replyText: "ok", action: null }),
}));

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
    expect(apiPostJson).toHaveBeenCalled();
    expect(result.current.lastTranscript).toBe("hello");
  });
});
