import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import { useAuth } from "@/context/AuthContext";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Lock, Mail, ShieldCheck, Eye, EyeOff, Loader2 } from "lucide-react";
import { toast } from "sonner";

export default function Login() {
  const { login, formatApiError } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("admin@rdx.com");
  const [password, setPassword] = useState("admin123");
  const [remember, setRemember] = useState(true);
  const [showPw, setShowPw] = useState(false);
  const [loading, setLoading] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      await login(email, password, remember);
      toast.success("Welcome back", { description: "Signed in as admin." });
      navigate("/", { replace: true });
    } catch (err) {
      toast.error("Login failed", { description: formatApiError(err.response?.data?.detail) || err.message });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen w-full bg-[#0F172A] text-white relative overflow-hidden flex">
      {/* Animated aurora background */}
      <div className="aurora" />
      <div className="absolute inset-0 grid-bg opacity-40" />

      {/* Left visual */}
      <div className="hidden lg:flex flex-1 relative items-end p-14">
        <div
          className="absolute inset-0 opacity-30"
          style={{
            backgroundImage:
              "url(https://images.unsplash.com/photo-1492144534655-ae79c964c9d7?auto=format&fit=crop&w=1400&q=70)",
            backgroundSize: "cover",
            backgroundPosition: "center",
          }}
        />
        <div className="absolute inset-0 bg-gradient-to-tr from-[#0F172A] via-[#0F172A]/70 to-transparent" />
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6 }}
          className="relative max-w-md"
        >
          <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full glass text-xs text-slate-300 mb-6">
            <ShieldCheck className="h-3.5 w-3.5 text-blue-400" />
            Enterprise-grade Access
          </div>
          <h1 className="text-5xl font-bold tracking-tight leading-tight">
            Command your <span className="text-blue-400">Showroom</span> in real-time.
          </h1>
          <p className="mt-4 text-slate-400 leading-relaxed">
            RDX brings AI number plate recognition, live CCTV monitoring, and rich analytics into a single premium
            command center — designed for modern automobile dealerships.
          </p>
          <div className="mt-8 grid grid-cols-3 gap-3">
            {[
              { k: "AI", v: "Plate Recognition" },
              { k: "24/7", v: "Live Monitoring" },
              { k: "180d", v: "Data Retention" },
            ].map((f) => (
              <div key={f.k} className="glass rounded-xl p-3">
                <div className="text-xl font-semibold text-white">{f.k}</div>
                <div className="text-[11px] text-slate-400">{f.v}</div>
              </div>
            ))}
          </div>
        </motion.div>
      </div>

      {/* Login card */}
      <div className="flex-1 relative flex items-center justify-center p-6 md:p-10">
        <motion.form
          onSubmit={submit}
          initial={{ opacity: 0, y: 30 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="w-full max-w-md glass rounded-3xl p-8 md:p-10 border-white/10"
          data-testid="login-form"
        >
          <div className="flex items-center gap-3 mb-8">
            <div className="h-12 w-12 rounded-2xl bg-gradient-to-br from-blue-500 to-blue-700 flex items-center justify-center shadow-lg shadow-blue-900/40">
              <span className="text-white font-bold text-lg">R</span>
            </div>
            <div className="leading-tight">
              <div className="text-lg font-semibold">RDX Showroom</div>
              <div className="text-xs text-slate-400 tracking-wide">Vehicle Management System</div>
            </div>
          </div>

          <h2 className="text-2xl font-semibold">Admin Sign In</h2>
          <p className="text-slate-400 text-sm mt-1">Secure access — authorized personnel only.</p>

          <div className="mt-8 space-y-5">
            <div>
              <Label htmlFor="email" className="text-slate-300 text-xs uppercase tracking-widest">Email</Label>
              <div className="relative mt-2">
                <Mail className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-500" />
                <Input
                  id="email"
                  type="email"
                  data-testid="login-email-input"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  className="pl-9 h-11 bg-white/5 border-white/10 focus-visible:ring-blue-500/50 text-white placeholder:text-slate-500"
                  placeholder="admin@rdx.com"
                />
              </div>
            </div>

            <div>
              <Label htmlFor="password" className="text-slate-300 text-xs uppercase tracking-widest">Password</Label>
              <div className="relative mt-2">
                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-500" />
                <Input
                  id="password"
                  type={showPw ? "text" : "password"}
                  data-testid="login-password-input"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  className="pl-9 pr-10 h-11 bg-white/5 border-white/10 focus-visible:ring-blue-500/50 text-white"
                  placeholder="••••••••"
                />
                <button
                  type="button"
                  onClick={() => setShowPw((s) => !s)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 rounded-md text-slate-400 hover:text-white hover:bg-white/5"
                  data-testid="toggle-password-visibility"
                >
                  {showPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>

            <div className="flex items-center justify-between text-sm">
              <label className="flex items-center gap-2 select-none cursor-pointer">
                <Checkbox
                  checked={remember}
                  onCheckedChange={(v) => setRemember(!!v)}
                  data-testid="remember-me-checkbox"
                  className="border-white/20 data-[state=checked]:bg-blue-600 data-[state=checked]:border-blue-600"
                />
                <span className="text-slate-300">Remember me</span>
              </label>
              <button
                type="button"
                onClick={() => toast.info("Password reset link would be emailed to your admin address.")}
                className="text-blue-400 hover:text-blue-300 text-xs"
                data-testid="forgot-password-btn"
              >
                Forgot password?
              </button>
            </div>

            <Button
              type="submit"
              disabled={loading}
              className="w-full h-11 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-xl shadow-lg shadow-blue-900/40"
              data-testid="login-submit-btn"
            >
              {loading ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin mr-2" /> Signing in...
                </>
              ) : (
                <>
                  <ShieldCheck className="h-4 w-4 mr-2" /> Secure Login
                </>
              )}
            </Button>

            <div className="text-[11px] text-slate-500 text-center pt-2 border-t border-white/5">
              Demo: <span className="text-slate-300">admin@rdx.com</span> / <span className="text-slate-300">admin123</span>
            </div>
          </div>
        </motion.form>
      </div>
    </div>
  );
}
