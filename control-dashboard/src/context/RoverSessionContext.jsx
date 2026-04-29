import { createContext, useCallback, useContext, useState } from "react";

const STORAGE_KEY = "roverSessionCreds";

const RoverSessionContext = createContext(null);

export function RoverSessionProvider({ children }) {
  const [sessionCreds, setSessionCreds] = useState(() => {
    if (typeof window === "undefined") return null;
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  });
  const [isAuthenticated, setIsAuthenticated] = useState(
    () => sessionCreds != null,
  );

  const login = useCallback((creds) => {
    setSessionCreds(creds);
    setIsAuthenticated(true);
    try {
      if (typeof window !== "undefined") {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(creds));
      }
    } catch {
      // ignore storage errors
    }
  }, []);

  const logout = useCallback(() => {
    setSessionCreds(null);
    setIsAuthenticated(false);
    try {
      if (typeof window !== "undefined") {
        window.localStorage.removeItem(STORAGE_KEY);
      }
    } catch {
      // ignore storage errors
    }
  }, []);

  const value = { isAuthenticated, sessionCreds, login, logout };

  return (
    <RoverSessionContext.Provider value={value}>
      {children}
    </RoverSessionContext.Provider>
  );
}

export function useRoverSession() {
  const ctx = useContext(RoverSessionContext);
  if (!ctx) throw new Error("useRoverSession must be used within RoverSessionProvider");
  return ctx;
}
