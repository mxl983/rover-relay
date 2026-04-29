import { useCallback, useMemo, useRef, useState } from "react";
import { apiPostJson } from "../api/client";
import {
  PI_VOICE_ENDPOINT,
  VOICE_RECOGNITION_LANG,
  VOICE_RECOGNITION_LIVE_LANG,
} from "../config";

function getRecognitionCtor() {
  if (typeof window === "undefined") return null;
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

function applyRecognitionLang(recognition, isLiveMode) {
  recognition.lang = isLiveMode
    ? VOICE_RECOGNITION_LIVE_LANG || VOICE_RECOGNITION_LANG
    : VOICE_RECOGNITION_LANG;
}

export function useVoiceAssistant({ onAction } = {}) {
  const [isListening, setIsListening] = useState(false);
  const [isThinking, setIsThinking] = useState(false);
  const [isSupported] = useState(() => Boolean(getRecognitionCtor()));
  const [lastTranscript, setLastTranscript] = useState("");
  const [assistantReply, setAssistantReply] = useState("");
  const [voiceError, setVoiceError] = useState("");
  const [isLiveMode, setIsLiveMode] = useState(false);

  const recognitionRef = useRef(null);
  const finalTextRef = useRef("");
  const interimTextRef = useRef("");
  const submitOnEndRef = useRef(false);
  const liveModeRef = useRef(false);
  const liveSubmitTimerRef = useRef(null);
  const lastActionSigRef = useRef("");
  const lastActionAtRef = useRef(0);

  const resetBuffers = () => {
    finalTextRef.current = "";
    interimTextRef.current = "";
  };

  const clearLiveSubmitTimer = () => {
    if (liveSubmitTimerRef.current != null) {
      clearTimeout(liveSubmitTimerRef.current);
      liveSubmitTimerRef.current = null;
    }
  };

  const stopRecognitionInstance = useCallback(() => {
    const r = recognitionRef.current;
    if (!r) return;
    try {
      r.stop();
    } catch {
      // no-op
    }
  }, []);

  const shouldThrottleAction = (action) => {
    const sig = JSON.stringify(action || null);
    const now = Date.now();
    const same = sig === lastActionSigRef.current;
    const tooSoon = now - lastActionAtRef.current < 900;
    if (same && tooSoon) return true;
    lastActionSigRef.current = sig;
    lastActionAtRef.current = now;
    return false;
  };

  const submitTranscript = useCallback(
    async (text) => {
      const transcript = String(text || "").trim();
      if (!transcript) return;
      setLastTranscript(transcript);
      setIsThinking(true);
      try {
        const data = await apiPostJson(
          PI_VOICE_ENDPOINT,
          { transcript },
          { retries: 0, timeout: 18_000 },
        );
        const replyText = data?.replyText || "收到。";
        setAssistantReply(replyText);
        const action = data?.action ?? null;
        if (action && onAction && !shouldThrottleAction(action)) {
          onAction(action);
        }
      } catch (err) {
        setVoiceError(err.message ?? "Voice interpret failed");
      } finally {
        setIsThinking(false);
      }
    },
    [onAction],
  );

  const sendText = useCallback(
    async (text) => {
      setVoiceError("");
      await submitTranscript(text);
    },
    [submitTranscript],
  );

  const flushLiveTranscript = useCallback(() => {
    const t = `${finalTextRef.current}${interimTextRef.current}`.trim();
    finalTextRef.current = "";
    interimTextRef.current = "";
    if (t) void submitTranscript(t);
  }, [submitTranscript]);

  const ensureRecognition = useCallback(() => {
    if (recognitionRef.current) return recognitionRef.current;
    const Ctor = getRecognitionCtor();
    if (!Ctor) return null;
    const r = new Ctor();
    applyRecognitionLang(r, liveModeRef.current);
    r.continuous = true;
    r.interimResults = true;
    r.maxAlternatives = 1;

    r.onresult = (event) => {
      let finalChunk = "";
      let interimChunk = "";
      let hadFinal = false;
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const item = event.results[i];
        const txt = item?.[0]?.transcript ?? "";
        if (item.isFinal) {
          finalChunk += txt;
          hadFinal = true;
        } else interimChunk += txt;
      }
      if (finalChunk) finalTextRef.current += finalChunk;
      interimTextRef.current = interimChunk;

      // Live chat: after user pauses, submit accumulated speech (final + interim).
      if (liveModeRef.current && (hadFinal || interimChunk)) {
        clearLiveSubmitTimer();
        liveSubmitTimerRef.current = setTimeout(() => {
          liveSubmitTimerRef.current = null;
          flushLiveTranscript();
        }, 900);
      }
    };

    r.onerror = (event) => {
      if (event?.error && event.error !== "no-speech") {
        setVoiceError(`Voice error: ${event.error}`);
      }
    };

    r.onend = () => {
      setIsListening(false);
      const wasLive = liveModeRef.current;
      const shouldSubmitPtt = submitOnEndRef.current && !wasLive;
      submitOnEndRef.current = false;
      const text = `${finalTextRef.current}${interimTextRef.current}`.trim();
      resetBuffers();
      if (shouldSubmitPtt && text) {
        void submitTranscript(text);
      }
      // In live mode, browsers may end unexpectedly; flush buffered text once.
      if (wasLive && text) {
        clearLiveSubmitTimer();
        void submitTranscript(text);
      }
      // Continuous mode: Chrome often ends the session after silence; restart if still live.
      if (wasLive) {
        setTimeout(() => {
          if (!liveModeRef.current || !recognitionRef.current) return;
          try {
            applyRecognitionLang(recognitionRef.current, true);
            recognitionRef.current.start();
            setIsListening(true);
          } catch {
            // Already started or not allowed; ignore
          }
        }, 120);
      }
    };

    recognitionRef.current = r;
    return r;
  }, [flushLiveTranscript, submitTranscript]);

  const startListening = useCallback(() => {
    setVoiceError("");
    if (liveModeRef.current) {
      liveModeRef.current = false;
      setIsLiveMode(false);
      clearLiveSubmitTimer();
    }
    const r = ensureRecognition();
    if (!r) {
      setVoiceError("Browser does not support speech recognition");
      return;
    }
    resetBuffers();
    submitOnEndRef.current = true;
    try {
      applyRecognitionLang(r, false);
      r.start();
      setIsListening(true);
    } catch {
      // Some browsers throw if start is called while already active.
      setIsListening(true);
    }
  }, [ensureRecognition]);

  const stopListening = useCallback(() => {
    stopRecognitionInstance();
  }, [stopRecognitionInstance]);

  const startLiveListening = useCallback(() => {
    setVoiceError("");
    const r = ensureRecognition();
    if (!r) {
      setVoiceError("Browser does not support speech recognition");
      return;
    }
    liveModeRef.current = true;
    setIsLiveMode(true);
    submitOnEndRef.current = false;
    clearLiveSubmitTimer();
    resetBuffers();

    const startOnce = () => {
      if (!liveModeRef.current) return;
      try {
        applyRecognitionLang(r, true);
        r.start();
        setIsListening(true);
      } catch {
        setIsListening(false);
      }
    };

    // If recognition is already active (e.g. PTT), stop() → onend restarts live.
    // If idle, stop() usually throws → start immediately.
    let startedFromIdle = false;
    try {
      r.stop();
    } catch {
      startOnce();
      startedFromIdle = true;
    }
    // If stop() no-ops without throwing, onend may never run — kick start shortly after.
    if (!startedFromIdle) {
      setTimeout(() => {
        if (!liveModeRef.current) return;
        try {
          applyRecognitionLang(r, true);
          r.start();
          setIsListening(true);
        } catch {
          // Already running from onend restart — ignore
        }
      }, 380);
    }
  }, [ensureRecognition]);

  const stopLiveListening = useCallback(() => {
    liveModeRef.current = false;
    setIsLiveMode(false);
    clearLiveSubmitTimer();
    stopRecognitionInstance();
  }, [stopRecognitionInstance]);

  const setLiveMode = useCallback(
    (enabled) => {
      if (enabled) startLiveListening();
      else stopLiveListening();
    },
    [startLiveListening, stopLiveListening],
  );

  const clearVoiceUi = useCallback(() => {
    setAssistantReply("");
    setVoiceError("");
    setLastTranscript("");
    setIsThinking(false);
  }, []);

  return useMemo(
    () => ({
      isSupported,
      isListening,
      isLiveMode,
      isThinking,
      lastTranscript,
      assistantReply,
      voiceError,
      startListening,
      stopListening,
      startLiveListening,
      stopLiveListening,
      setLiveMode,
      sendText,
      clearVoiceUi,
    }),
    [
      isSupported,
      isListening,
      isLiveMode,
      isThinking,
      lastTranscript,
      assistantReply,
      voiceError,
      startListening,
      stopListening,
      startLiveListening,
      stopLiveListening,
      setLiveMode,
      sendText,
      clearVoiceUi,
    ],
  );
}

