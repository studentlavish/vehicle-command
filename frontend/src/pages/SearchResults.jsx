import React, { useEffect, useState, useCallback } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import api from "@/lib/api";
import { motion } from "framer-motion";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Search, Car, ArrowRight, Fingerprint, Phone, User } from "lucide-react";

export default function SearchResults() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const [q, setQ] = useState(params.get("q") || "");
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);

  const runSearch = useCallback(async (query) => {
    if (!query.trim()) { setResults([]); return; }
    setLoading(true);
    try {
      const { data } = await api.get("/vehicles/search", { params: { q: query } });
      setResults(data);
      // If single exact-match on vehicle_number, jump straight to details
      if (data.length === 1) {
        navigate(`/vehicle/${encodeURIComponent(data[0].vehicle_number)}`, { replace: true });
      }
    } finally {
      setLoading(false);
    }
  }, [navigate]);

  useEffect(() => {
    const initial = params.get("q") || "";
    setQ(initial);
    runSearch(initial);
  }, [params, runSearch]);

  const onSubmit = (e) => {
    e.preventDefault();
    setParams({ q });
  };

  return (
    <div className="space-y-6" data-testid="search-page">
      <div>
        <div className="inline-flex items-center gap-2 text-xs uppercase tracking-[0.25em] text-blue-400/80">
          <Search className="h-3.5 w-3.5" /> Lookup
        </div>
        <h1 className="mt-2 text-4xl md:text-5xl font-bold tracking-tight">Search Vehicles</h1>
        <p className="text-slate-400 mt-2 text-sm">Find any vehicle by plate number, owner name, phone number or customer ID.</p>
      </div>

      <form onSubmit={onSubmit} className="glass rounded-2xl p-4">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
          <Input
            data-testid="search-input"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="e.g. UP21AB1234, Aarav Malhotra, +91 9876543210, CUS-DEMO01"
            className="pl-9 h-12 bg-white/5 border-white/10 text-white placeholder:text-slate-500 text-base"
            autoFocus
          />
          <Button type="submit" className="absolute right-2 top-1/2 -translate-y-1/2 h-8 bg-blue-600 hover:bg-blue-700" data-testid="search-submit">
            Search
          </Button>
        </div>
      </form>

      {loading ? (
        <div className="grid gap-4 md:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={`sr-skel-${i}`} className="h-32 bg-white/5 rounded-2xl" />)}
        </div>
      ) : results.length === 0 ? (
        <div className="glass rounded-2xl p-14 text-center text-slate-400" data-testid="search-empty">
          {params.get("q") ? "No vehicles matched your query." : "Type a plate, owner, phone or customer ID to begin."}
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {results.map((r, idx) => (
            <motion.button
              key={r.id}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.35, delay: idx * 0.04 }}
              onClick={() => navigate(`/vehicle/${encodeURIComponent(r.vehicle_number)}`)}
              className="glass rounded-2xl p-5 text-left hover:-translate-y-1 transition-all duration-300 group"
              data-testid={`search-result-${r.vehicle_number}`}
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-start gap-3 min-w-0">
                  <div className="h-14 w-14 rounded-xl overflow-hidden bg-slate-800 border border-white/10 shrink-0">
                    {r.vehicle_image ? (
                      <img src={r.vehicle_image} alt={r.vehicle_number} className="w-full h-full object-cover" />
                    ) : (
                      <div className="w-full h-full flex items-center justify-center"><Car className="h-6 w-6 text-slate-500" /></div>
                    )}
                  </div>
                  <div className="min-w-0">
                    <div className="font-mono text-lg text-white">{r.vehicle_number}</div>
                    <div className="text-sm text-slate-300 flex items-center gap-1 mt-1"><User className="h-3 w-3 text-blue-400" />{r.owner_name}</div>
                    <div className="text-xs text-slate-400 flex items-center gap-1 mt-0.5"><Phone className="h-3 w-3" />{r.phone_number || "—"}</div>
                    <div className="text-[11px] text-slate-500 flex items-center gap-1 mt-0.5"><Fingerprint className="h-3 w-3" />{r.customer_id}</div>
                  </div>
                </div>
                <ArrowRight className="h-4 w-4 text-slate-500 group-hover:text-blue-400 transition-colors mt-1" />
              </div>
              <div className="mt-4 flex items-center gap-2 text-[11px]">
                <Badge className="bg-blue-500/15 text-blue-300 border border-blue-500/30">{r.total_visits} total visits</Badge>
                {r.active_visits > 0 && (
                  <Badge className="bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
                    <span className="pulse-dot text-emerald-400 mr-1">{r.active_visits} inside</span>
                  </Badge>
                )}
              </div>
            </motion.button>
          ))}
        </div>
      )}
    </div>
  );
}
