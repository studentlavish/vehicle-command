import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import Logo from "@/components/common/Logo";
import { Lock, Mail, Eye, EyeOff, Loader2, ScanLine, Video, BarChart3, ShieldCheck, AlertCircle } from "lucide-react";
import { toast } from "sonner";

const IS_DEV = process.env.NODE_ENV === "development";

// Local development only: pre-fills the admin email (never the password).
const DEMO = [{ role: "Admin", email: "admin@rdx.com" }];

const FEATURES = [
  { icon: ScanLine, title: "Number plate recognition", text: "Entries and exits logged automatically from camera feeds." },
  { icon: Video, title: "Live camera monitoring", text: "Every gate and showroom camera in one view." },
  { icon: BarChart3, title: "Traffic analytics", text: "Daily, weekly and monthly visitor trends and reports." },
];

export default function Login() {
  const { login, formatApiError } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState(IS_DEV ? DEMO[0].email : "");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [showPw, setShowPw] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const user = await login(email.trim(), password, remember);
      toast.success(`Welcome back${user?.name ? `, ${user.name.split(" ")[0]}` : ""}`);
      navigate("/", { replace: true });
    } catch (err) {
      const msg = err.code === "ERR_NETWORK"
        ? "Can't reach the server. Make sure the backend is running."
        : formatApiError(err.response?.data?.detail) || err.message;
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const google = () => {
    // REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
    const redirectUrl = window.location.origin + "/";
    window.location.href = `https://auth.emergentagent.com/?redirect=${encodeURIComponent(redirectUrl)}`;
  };

  return (
    <div className="flex min-h-screen w-full bg-background">
      {/* Brand panel */}
      <div className="relative hidden w-[46%] flex-col justify-between overflow-hidden bg-primary p-12 text-primary-foreground lg:flex">
        <div className="pointer-events-none absolute -right-32 -top-32 h-96 w-96 rounded-full bg-white/10" />
        <div className="pointer-events-none absolute -bottom-40 -left-24 h-[28rem] w-[28rem] rounded-full bg-white/5" />
        <div className="relative flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-white/15 ring-1 ring-white/25">
            <ScanLine className="h-5 w-5" />
          </div>
          <div className="leading-tight">
            <div className="font-semibold">Vashu Hyundai</div>
            <div className="text-xs text-white/70">Vehicle Management System</div>
          </div>
        </div>

        <div className="relative max-w-md">
          <h1 className="text-4xl font-semibold leading-tight tracking-tight text-white">
            Every vehicle, every visit — tracked automatically.
          </h1>
          <p className="mt-4 text-white/75">
            One dashboard for showroom traffic, live cameras and customer visit history.
          </p>
          <ul className="mt-10 space-y-5">
            {FEATURES.map((f) => (
              <li key={f.title} className="flex gap-4">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-white/15">
                  <f.icon className="h-5 w-5" />
                </div>
                <div>
                  <div className="font-medium text-white">{f.title}</div>
                  <div className="text-sm text-white/70">{f.text}</div>
                </div>
              </li>
            ))}
          </ul>
        </div>

        <div className="relative flex items-center gap-2 text-xs text-white/60">
          <ShieldCheck className="h-4 w-4" /> Secure, role-based access for your team
        </div>
      </div>

      {/* Form */}
      <div className="dot-grid flex flex-1 items-center justify-center p-6">
        <div className="w-full max-w-[400px]">
          <div className="mb-8 lg:hidden"><Logo size="lg" /></div>

          <div className="surface p-8 shadow-pop">
            <h2 className="text-xl font-semibold">Sign in</h2>
            <p className="mt-1 text-sm text-muted-foreground">Enter your credentials to access the dashboard.</p>

            <form onSubmit={submit} className="mt-6 space-y-4" data-testid="login-form">
              {error && (
                <div className="flex items-start gap-2 rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2.5 text-sm text-destructive" role="alert">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> {error}
                </div>
              )}

              <div>
                <label htmlFor="email" className="field-label">Email</label>
                <div className="relative">
                  <Mail className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input id="email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required className="h-11 pl-9" placeholder="you@company.com" data-testid="login-email-input" />
                </div>
              </div>

              <div>
                <label htmlFor="password" className="field-label">Password</label>
                <div className="relative">
                  <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input id="password" type={showPw ? "text" : "password"} autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required className="h-11 pl-9 pr-10" placeholder="••••••••" data-testid="login-password-input" />
                  <button type="button" onClick={() => setShowPw((s) => !s)} className="absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={showPw ? "Hide password" : "Show password"} data-testid="toggle-password-visibility">
                    {showPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
              </div>

              <div className="flex items-center justify-between text-sm">
                <label className="flex cursor-pointer select-none items-center gap-2">
                  <Checkbox checked={remember} onCheckedChange={(v) => setRemember(!!v)} data-testid="remember-me-checkbox" />
                  <span className="text-muted-foreground">Keep me signed in</span>
                </label>
                <button type="button" onClick={() => toast.info("Ask an administrator to reset your password.")} className="text-sm font-medium text-primary hover:underline" data-testid="forgot-password-btn">
                  Forgot password?
                </button>
              </div>

              <Button type="submit" size="lg" disabled={loading} className="w-full" data-testid="login-submit-btn">
                {loading ? <><Loader2 className="h-4 w-4 animate-spin" /> Signing in…</> : "Sign in"}
              </Button>

              <div className="flex items-center gap-3 py-1">
                <div className="h-px flex-1 bg-border" />
                <span className="text-xs text-muted-foreground">or</span>
                <div className="h-px flex-1 bg-border" />
              </div>

              <Button type="button" variant="outline" size="lg" onClick={google} className="w-full" data-testid="google-signin-btn">
                <svg className="h-4 w-4" viewBox="0 0 24 24" aria-hidden="true">
                  <path fill="#4285F4" d="M23.49 12.27c0-.79-.07-1.54-.19-2.27H12v4.51h6.47c-.29 1.48-1.14 2.73-2.4 3.58v3h3.86c2.26-2.09 3.56-5.17 3.56-8.82z" />
                  <path fill="#34A853" d="M12 24c3.24 0 5.95-1.08 7.93-2.91l-3.86-3c-1.08.72-2.45 1.16-4.07 1.16-3.13 0-5.78-2.11-6.73-4.96H1.29v3.09C3.26 21.3 7.31 24 12 24z" />
                  <path fill="#FBBC05" d="M5.27 14.29c-.25-.72-.38-1.49-.38-2.29s.14-1.57.38-2.29V6.62H1.29C.47 8.24 0 10.06 0 12s.47 3.76 1.29 5.38l3.98-3.09z" />
                  <path fill="#EA4335" d="M12 4.75c1.77 0 3.35.61 4.6 1.8l3.42-3.42C17.95 1.19 15.24 0 12 0 7.31 0 3.26 2.7 1.29 6.62l3.98 3.09C6.22 6.86 8.87 4.75 12 4.75z" />
                </svg>
                Continue with Google
              </Button>
            </form>
          </div>

        </div>
      </div>
    </div>
  );
}
