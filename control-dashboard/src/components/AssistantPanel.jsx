import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Lightbulb } from "lucide-react";

export function AssistantPanel({
  videoStreamReady,
  voiceSupported,
  isListening,
  isLiveMode,
  isThinking,
  transcript,
  reply,
  error,
  onSendText,
  onSetLiveMode,
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [history, setHistory] = useState([]);
  const lastSeenRef = useRef({ transcript: "", reply: "", error: "" });
  const listRef = useRef(null);

  useEffect(() => {
    const next = [];
    if (error && error !== lastSeenRef.current.error) {
      next.push({ role: "system", text: `Error: ${error}`, tone: "error" });
      lastSeenRef.current.error = error;
    }
    if (transcript && transcript !== lastSeenRef.current.transcript) {
      next.push({ role: "user", text: transcript, tone: "normal" });
      lastSeenRef.current.transcript = transcript;
    }
    if (reply && reply !== lastSeenRef.current.reply) {
      next.push({ role: "assistant", text: reply, tone: "normal" });
      lastSeenRef.current.reply = reply;
    }
    if (next.length) {
      setHistory((prev) => [...prev, ...next].slice(-40));
    }
  }, [transcript, reply, error]);

  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [history, isThinking, isListening]);

  const sendTextNow = async () => {
    const msg = text.trim();
    if (!msg) return;
    setText("");
    await onSendText?.(msg);
  };

  useEffect(() => {
    if (!open) onSetLiveMode?.(false);
  }, [open, onSetLiveMode]);

  return (
    <>
      <style>{`
        @keyframes assistant-typing-dot {
          0%, 70%, 100% { transform: translateY(0); opacity: 0.35; }
          35% { transform: translateY(-5px); opacity: 1; }
        }
        .assistant-typing-indicator {
          display: inline-flex;
          align-items: center;
          gap: 8px;
        }
        .assistant-typing-label {
          font-size: 11px;
          color: #9eefff;
        }
        .assistant-typing-dots {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          height: 14px;
        }
        .assistant-typing-dots span {
          width: 5px;
          height: 5px;
          border-radius: 50%;
          background: #9eefff;
          animation: assistant-typing-dot 1.05s ease-in-out infinite;
        }
        .assistant-typing-dots span:nth-child(2) {
          animation-delay: 0.16s;
        }
        .assistant-typing-dots span:nth-child(3) {
          animation-delay: 0.32s;
        }
        @media (prefers-reduced-motion: reduce) {
          .assistant-typing-dots span {
            animation: none;
            opacity: 0.65;
          }
        }
        @keyframes assistant-fab-pulse {
          0%, 100% { box-shadow: 0 0 0 0 rgba(255,138,0,0.42); }
          50% { box-shadow: 0 0 0 10px rgba(255,138,0,0.0); }
        }
        @keyframes assistant-live-rec-dot {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.4; transform: scale(0.92); }
        }
        .assistant-live-status {
          display: inline-flex;
          align-items: center;
          gap: 8px;
        }
        .assistant-live-rec-dot {
          width: 8px;
          height: 8px;
          border-radius: 50%;
          background: #ff3b30;
          box-shadow: 0 0 6px rgba(255, 59, 48, 0.85);
          flex-shrink: 0;
          animation: assistant-live-rec-dot 0.9s ease-in-out infinite;
        }
        @media (prefers-reduced-motion: reduce) {
          .assistant-live-rec-dot {
            animation: none;
            opacity: 1;
          }
        }
        .assistant-panel-close {
          position: absolute;
          top: 6px;
          right: 6px;
          z-index: 2;
          width: 28px;
          height: 28px;
          padding: 0;
          margin: 0;
          display: flex;
          align-items: center;
          justify-content: center;
          border-radius: 8px;
          border: 1px solid rgba(255,255,255,0.22);
          background: rgba(0,0,0,0.35);
          color: #c8eef8;
          font-size: 18px;
          line-height: 1;
          cursor: pointer;
          -webkit-tap-highlight-color: transparent;
        }
        .assistant-panel-close:hover {
          background: rgba(255,255,255,0.12);
          color: #fff;
        }
        .assistant-send-btn {
          box-sizing: border-box;
          align-self: center;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-height: 0;
          height: 30px;
          min-width: 52px;
          padding: 0 12px;
          margin: 0;
          font-size: 11px;
          font-weight: 600;
          line-height: 1;
          letter-spacing: normal;
          text-align: center;
          border-radius: 6px;
          border: 1px solid rgba(0,242,255,0.5);
          background: rgba(0,242,255,0.12);
          color: #9eefff;
          cursor: pointer;
          -webkit-tap-highlight-color: transparent;
        }
        .assistant-send-btn:active {
          transform: scale(0.97);
        }
        .assistant-send-btn:disabled {
          opacity: 0.45;
          cursor: not-allowed;
        }
        .assistant-live-btn {
          box-sizing: border-box;
          align-self: center;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          height: 30px;
          min-width: 44px;
          padding: 0 8px;
          margin: 0;
          font-size: 10px;
          font-weight: 800;
          letter-spacing: 0.06em;
          line-height: 1;
          border-radius: 6px;
          border: 1px solid rgba(255,255,255,0.28);
          background: rgba(255,255,255,0.08);
          color: #c8e8f0;
          cursor: pointer;
          -webkit-tap-highlight-color: transparent;
        }
        .assistant-live-btn.on {
          border-color: rgba(34,197,94,0.85);
          background: rgba(34,197,94,0.2);
          color: #b6f7c8;
          box-shadow: 0 0 12px rgba(34,197,94,0.25);
        }
        .assistant-live-btn:disabled {
          opacity: 0.4;
          cursor: not-allowed;
        }
        /* Beat hud.css .glass-card (letter-spacing: 2px, padding 12px 18px) */
        .glass-card.assistant-fab {
          box-sizing: border-box;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 42px;
          height: 42px;
          min-width: 42px;
          min-height: 42px;
          padding: 0 !important;
          letter-spacing: normal !important;
        }
      `}</style>

      {!open && videoStreamReady && (
        <button
          type="button"
          className="glass-card assistant-fab"
          onClick={() => setOpen(true)}
          aria-label="Open assistant panel"
          title="Assistant"
          style={{
            position: "fixed",
            right: "max(14px, env(safe-area-inset-right))",
            top: "max(78px, env(safe-area-inset-top))",
            zIndex: 1003,
            borderColor: "rgba(255,138,0,0.85)",
            color: "#ffd180",
            background: "linear-gradient(135deg, rgba(255,138,0,0.25), rgba(255,62,116,0.22))",
            animation: "assistant-fab-pulse 1.8s ease-out infinite",
          }}
        >
          <Lightbulb
            size={22}
            strokeWidth={1.75}
            aria-hidden
          />
        </button>
      )}

      {open && (
        <div
          className="glass-card"
          style={{
            position: "fixed",
            right: "max(14px, env(safe-area-inset-right))",
            top: "max(118px, env(safe-area-inset-top))",
            zIndex: 1002,
            width: "min(92vw, 380px)",
            padding: 8,
            paddingTop: 10,
            display: "flex",
            flexDirection: "column",
            gap: 6,
            maxHeight: "min(72vh, 560px)",
          }}
        >
          <button
            type="button"
            className="assistant-panel-close"
            onClick={() => setOpen(false)}
            aria-label="Close chat"
            title="Close"
          >
            ×
          </button>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "flex-start",
              padding: "2px 36px 2px 0",
            }}
          >
            <strong
              style={{
                color: "#9eefff",
                fontSize: 12,
                letterSpacing: "0.08em",
              }}
            >
              Mango Mate Chat
            </strong>
          </div>

          <div
            ref={listRef}
            style={{
              flex: 1,
              minHeight: 210,
              maxHeight: 360,
              overflowY: "auto",
              display: "flex",
              flexDirection: "column",
              gap: 6,
              padding: "2px 1px 2px 1px",
            }}
          >
            {history.length === 0 && !isListening && !isThinking && (
              <div style={{ color: "rgba(180,225,240,0.8)", fontSize: 12, opacity: 0.75 }}>
                Type a command, or tap LIVE and speak hands-free.
              </div>
            )}
            {history.map((m, idx) => {
              const isUser = m.role === "user";
              const isErr = m.tone === "error";
              return (
                <div
                  key={`${m.role}-${idx}-${m.text.slice(0, 12)}`}
                  style={{
                    alignSelf: isUser ? "flex-end" : "flex-start",
                    maxWidth: "90%",
                    whiteSpace: "pre-line",
                    borderRadius: 10,
                    padding: "6px 8px",
                    fontSize: 12,
                    lineHeight: 1.35,
                    background: isErr
                      ? "rgba(255,68,68,0.18)"
                      : isUser
                        ? "rgba(0,242,255,0.16)"
                        : "rgba(255,255,255,0.08)",
                    border: `1px solid ${
                      isErr
                        ? "rgba(255,68,68,0.45)"
                        : isUser
                          ? "rgba(0,242,255,0.45)"
                          : "rgba(255,255,255,0.16)"
                    }`,
                    color: isErr ? "#ff8b8d" : "#d8f6ff",
                  }}
                >
                  {isUser ? "You: " : m.role === "assistant" ? "Mango Mate: " : ""}
                  {m.text}
                </div>
              );
            })}
          </div>

          <div
            style={{
              minHeight: 24,
              display: "flex",
              alignItems: "center",
              padding: "0 2px",
            }}
          >
            <div
              style={{
                visibility: isListening || isThinking || isLiveMode ? "visible" : "hidden",
                borderRadius: 8,
                padding: "3px 8px",
                fontSize: 11,
                background: "rgba(255,255,255,0.08)",
                border: "1px solid rgba(255,255,255,0.16)",
                color: "#9eefff",
              }}
            >
              <span className="assistant-live-status">
                {isLiveMode && isListening && !isThinking ? (
                  <span
                    className="assistant-live-rec-dot"
                    role="img"
                    aria-label="Listening"
                    title="Listening"
                  />
                ) : null}
                {isThinking ? (
                  <span className="assistant-typing-indicator">
                    <span className="assistant-typing-label">Mango Mate is typing</span>
                    <span className="assistant-typing-dots" aria-hidden="true">
                      <span />
                      <span />
                      <span />
                    </span>
                  </span>
                ) : (
                  <span>
                    {isLiveMode
                      ? isListening
                        ? "Live — speak anytime"
                        : "Live — connecting mic…"
                      : "Listening..."}
                  </span>
                )}
              </span>
            </div>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr auto auto",
              gap: 6,
              alignItems: "center",
            }}
          >
            <input
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Type Mandarin command..."
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  void sendTextNow();
                }
              }}
              style={{
                minWidth: 0,
                background: "rgba(0,0,0,0.45)",
                color: "#d8f6ff",
                border: "1px solid rgba(0,242,255,0.45)",
                borderRadius: 6,
                padding: "6px 8px",
                outline: "none",
              }}
            />
            <button
              type="button"
              className={`assistant-live-btn${isLiveMode ? " on" : ""}`}
              disabled={!voiceSupported}
              aria-pressed={isLiveMode}
              title={
                voiceSupported
                  ? isLiveMode
                    ? "Turn off continuous listening"
                    : "Listen continuously — speak without holding a button"
                  : "Speech recognition not supported in this browser"
              }
              onClick={() => onSetLiveMode?.(!isLiveMode)}
            >
              LIVE
            </button>
            <button
              type="button"
              className="assistant-send-btn"
              onClick={() => void sendTextNow()}
            >
              Send
            </button>
          </div>
        </div>
      )}
    </>
  );
}

AssistantPanel.propTypes = {
  videoStreamReady: PropTypes.bool,
  voiceSupported: PropTypes.bool,
  isListening: PropTypes.bool,
  isLiveMode: PropTypes.bool,
  isThinking: PropTypes.bool,
  transcript: PropTypes.string,
  reply: PropTypes.string,
  error: PropTypes.string,
  onSendText: PropTypes.func,
  onSetLiveMode: PropTypes.func,
};

