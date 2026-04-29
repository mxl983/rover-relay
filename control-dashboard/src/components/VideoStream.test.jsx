import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { VideoStream } from "./VideoStream.jsx";

describe("VideoStream", () => {
  const OrigPC = globalThis.RTCPeerConnection;
  const OrigFetch = globalThis.fetch;

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      text: async () => "v=0\r\no=- 0 0 IN IP4 0.0.0.0\r\n",
    });
    globalThis.RTCPeerConnection = vi.fn().mockImplementation(() => {
      const pc = {
        createOffer: vi.fn().mockResolvedValue({ type: "offer", sdp: "o" }),
        setLocalDescription: vi.fn().mockImplementation(async (d) => {
          pc.localDescription = d;
        }),
        setRemoteDescription: vi.fn().mockResolvedValue(undefined),
        close: vi.fn(),
        addTransceiver: vi.fn(),
        connectionState: "connected",
        onconnectionstatechange: null,
        ontrack: null,
        localDescription: null,
      };
      return pc;
    });
    const fakeStream = {
      getTracks: () => [{ stop: vi.fn() }],
      getAudioTracks: () => [{ stop: vi.fn() }],
    };
    globalThis.navigator.mediaDevices = {
      getUserMedia: vi.fn().mockResolvedValue(fakeStream),
    };
  });

  afterEach(() => {
    vi.useRealTimers();
    globalThis.RTCPeerConnection = OrigPC;
    globalThis.fetch = OrigFetch;
  });

  it("mounts and creates peer connection for video", async () => {
    const onReady = vi.fn();
    render(<VideoStream onVideoReadyChange={onReady} />);
    await waitFor(() => expect(globalThis.RTCPeerConnection).toHaveBeenCalled());
  });
});
