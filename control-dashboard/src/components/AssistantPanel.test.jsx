import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AssistantPanel } from "./AssistantPanel.jsx";

describe("AssistantPanel", () => {
  it("opens panel and sends text", async () => {
    const user = userEvent.setup();
    const onSendText = vi.fn();
    render(
      <AssistantPanel
        videoStreamReady
        voiceSupported={false}
        isListening={false}
        isLiveMode={false}
        isThinking={false}
        transcript=""
        reply=""
        error=""
        onSendText={onSendText}
        onSetLiveMode={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Open assistant panel" }));
    const input = screen.getByPlaceholderText(/Type Mandarin/i);
    await user.type(input, "hi");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(onSendText).toHaveBeenCalled();
  });
});
