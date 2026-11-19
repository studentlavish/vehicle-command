import React, { useEffect, useState, useCallback } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import api from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import PageHeader from "@/components/common/PageHeader";
import StatusBadge from "@/components/common/StatusBadge";
import { EmptyState, ErrorState } from "@/components/common/EmptyState";
import { assetUrl } from "@/lib/api";
import { Search, Car, ChevronRight, Fingerprint, Phone, User, SearchX, Loader2 } from "lucide-react";

const EXAMPLES = ["UP21AB1234", "Aarav", "98765", "CUS-"];

export default function SearchResults() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const query = params.get("q") || "";
  const [q, setQ] = useState(query);
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  const runSearch = useCallback(async (text) => {
    if (!text.trim()) { setResults([]); return; }
    setLoading(true);
    setError(false);
    try {
      const { data } = await api.get("/vehicles/search", { params: { q: text } });
      setResults(data);
      if (data.length === 1) navigate(`/vehicle/${encodeURIComponent(data[0].vehicle_number)}`, { replace: true });
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [navigate]);

  useEffect(() => { setQ(query); runSearch(query); }, [query, runSearch]);

  const onSubmit = (e) => { e.preventDefault(); if (q.trim()) setParams({ q: q.trim() }); };

  return (
    <div className="space-y-6" data-testid="search-page">
      <PageHeader icon={Search} title="Search vehicles" description="Find any vehicle by plate number, owner name, phone number or customer ID." />

      <form onSubmit={onSubmit} className="surface p-4">
        <div className="flex flex-col gap-3 sm:flex-row">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-3.5 top-1/2 h-5 w-5 -translate-y-1/2 text-muted-foreground" />
            <Input
              data-testid="search-input"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="e.g. UP21AB1234, Aarav Malhotra, 9876543210, CUS-DEMO01"
              className="h-12 pl-11 text-base"
              autoFocus
            />
          </div>
          <Button type="submit" size="lg" className="h-12" disabled={loading || !q.trim()} data-testid="search-submit">
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />} Search
          </Button>
        </div>
        {!query && (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            Try:
            {EXAMPLES.map((ex) => (
              <button key={ex} type="button" onClick={() => setParams({ q: ex })} className="rounded-full border bg-muted/60 px-2.5 py-1 font-mono text-foreground hover:border-primary hover:text-primary">
                {ex}
              </button>
            ))}
          </div>
        )}
      </form>

      {query && !loading && !error && (
        <div className="text-sm text-muted-foreground">
          {results.length} result{results.length === 1 ? "" : "s"} for <span className="font-medium text-foreground">“{query}”</span>
        </div>
      )}

      {loading ? (
        <div className="grid gap-4 md:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-36 rounded-xl" />)}
        </div>
      ) : error ? (
        <div className="surface"><ErrorState onRetry={() => runSearch(query)} /></div>
      ) : results.length === 0 ? (
        <div className="surface" data-testid="search-empty">
          {query ? (
            <EmptyState icon={SearchX} title="No matching vehicles" description="Check the spelling, or search with fewer characters (e.g. the last 4 digits of the plate)." />
          ) : (
            <EmptyState icon={Search} title="Start typing to search" description="Results include every vehicle seen in the last 180 days." />
          )}
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {results.map((r) => (
            <button
              key={r.id}
              onClick={() => navigate(`/vehicle/${encodeURIComponent(r.vehicle_number)}`)}
              className="surface surface-hover group p-5 text-left"
              data-testid={`search-result-${r.vehicle_number}`}
            >
              <div className="flex items-start gap-4">
                <div className="flex h-14 w-14 shrink-0 items-center justify-center overflow-hidden rounded-lg border bg-muted">
                  {r.vehicle_image ? (
                    <img src={assetUrl(r.vehicle_image)} alt={r.vehicle_number} className="h-full w-full object-cover" />
                  ) : (
                    <Car className="h-6 w-6 text-muted-foreground" />
                  )}
                </div>
                <div className="min-w-0 flex-1 space-y-1.5">
                  <span className="plate">{r.vehicle_number}</span>
                  <div className="flex items-center gap-1.5 text-sm font-medium"><User className="h-3.5 w-3.5 text-muted-foreground" />{r.owner_name}</div>
                  <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                    <span className="inline-flex items-center gap-1"><Phone className="h-3 w-3" />{r.phone_number || "—"}</span>
                    <span className="inline-flex items-center gap-1 font-mono"><Fingerprint className="h-3 w-3" />{r.customer_id}</span>
                  </div>
                </div>
                <ChevronRight className="mt-1 h-5 w-5 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-primary" />
              </div>
              <div className="mt-4 flex items-center gap-2 border-t pt-3">
                <StatusBadge tone="primary">{r.total_visits} visit{r.total_visits === 1 ? "" : "s"}</StatusBadge>
                {r.active_visits > 0 && <StatusBadge status="inside">Inside now</StatusBadge>}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
