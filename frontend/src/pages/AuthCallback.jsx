import React, { useEffect, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";

export default function AuthCallback() {
  const location = useLocation();
  const navigate = useNavigate();
  const { googleSession, formatApiError } = useAuth();
  const processed = useRef(false);

  useEffect(() => {
    if (processed.current) return;
    processed.current = true;
    const match = location.hash.match(/session_id=([^&]+)/);
    const sessionId = match ? match[1] : null;
    if (!sessionId) {
      navigate("/login", { replace: true });
      return;
    }
    (async () => {
      try {
        const user = await googleSession(sessionId);
        window.history.replaceState(null, "", window.location.pathname);
        toast.success("Welcome back", { description: `Signed in with Google as ${user.email}` });
        navigate("/", { replace: true, state: { user } });
      } catch (err) {
        toast.error("Google sign-in failed", {
          description: formatApiError(err.response?.data?.detail) || err.message,
        });
        navigate("/login", { replace: true });
      }
    })();
  }, [location.hash, googleSession, navigate, formatApiError]);

  return (
    <div className="min-h-screen w-full bg-[#0F172A] flex items-center justify-center" data-testid="auth-callback">
      <Loader2 className="h-6 w-6 animate-spin text-blue-400" />
    </div>
  );
}
