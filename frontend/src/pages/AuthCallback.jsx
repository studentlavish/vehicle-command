import React, { useEffect, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import Logo from "@/components/common/Logo";

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
        toast.error("Google sign-in failed", { description: formatApiError(err.response?.data?.detail) || err.message });
        navigate("/login", { replace: true });
      }
    })();
  }, [location.hash, googleSession, navigate, formatApiError]);

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-6 bg-background" data-testid="auth-callback">
      <Logo size="lg" />
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin text-primary" /> Signing you in…
      </div>
    </div>
  );
}
