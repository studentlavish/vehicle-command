import React, { createContext, useContext, useEffect, useState, useCallback } from "react";
import api, { formatApiError } from "@/lib/api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null); // null = checking, false = unauthenticated, object = authenticated
  const [loading, setLoading] = useState(true);

  const checkSession = useCallback(async () => {
    try {
      const { data } = await api.get("/auth/me");
      setUser(data);
    } catch {
      setUser(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // CRITICAL: returning from Emergent Google OAuth — AuthCallback exchanges
    // the session_id and establishes the session before any /auth/me check.
    if (window.location.hash?.includes("session_id=")) {
      setLoading(false);
      return;
    }
    checkSession();
  }, [checkSession]);

  const login = async (email, password, rememberMe) => {
    const { data } = await api.post("/auth/login", { email, password, remember_me: rememberMe });
    setUser(data);
    return data;
  };

  const googleSession = async (sessionId) => {
    const { data } = await api.post("/auth/google/session", { session_id: sessionId });
    setUser(data);
    return data;
  };

  const logout = async () => {
    try {
      await api.post("/auth/logout");
    } catch (error) {
      // Session may already be invalid; proceed with client-side clear.
      if (process.env.NODE_ENV === "development") {
        console.error("Logout error:", error);
      }
    }
    setUser(false);
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, googleSession, refresh: checkSession, formatApiError }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
