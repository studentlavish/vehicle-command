import React, { useCallback, useEffect, useState } from "react";
import api from "@/lib/api";
import PageHeader from "@/components/common/PageHeader";
import StatusBadge from "@/components/common/StatusBadge";
import VehiclePlate from "@/components/common/VehiclePlate";
import Pagination from "@/components/common/Pagination";
import { EmptyState, ErrorState } from "@/components/common/EmptyState";
import { TableSkeletonRows } from "@/components/common/Loaders";
import { LogIn as LogInIcon, LogOut } from "lucide-react";
import { formatDate, formatTime, elapsed } from "@/lib/time";

const PAGE_SIZE = 50;

export default function HistoryPage({ mode = "entry" }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [page, setPage] = useState(1);
  const [meta, setMeta] = useState({ total: 0, hasNext: false, hasPrev: false });
  const isExit = mode === "exit";

  const load = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const params = { page, page_size: PAGE_SIZE };
      if (isExit) params.status = "exited";
      const { data } = await api.get("/vehicles", { params });
      setRows(data.items || []);
      setMeta({ total: data.total || 0, hasNext: !!data.has_next, hasPrev: !!data.has_prev });
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [isExit, page]);

  useEffect(() => { setPage(1); }, [mode]);
  useEffect(() => { load(); }, [load]);

  const Icon = isExit ? LogOut : LogInIcon;

  return (
    <div className="space-y-6" data-testid={`${mode}-history-page`}>
      <PageHeader
        icon={Icon}
        title={isExit ? "Exit history" : "Entry history"}
        description={isExit ? "Every vehicle that has left the showroom, newest first." : "Every vehicle that has entered the showroom, newest first."}
      />

      <div className="surface overflow-hidden">
        {error ? (
          <ErrorState onRetry={load} />
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Vehicle</th>
                    <th>Owner</th>
                    <th>Contact</th>
                    <th>Date</th>
                    <th>{isExit ? "Exit time" : "Entry time"}</th>
                    <th>Duration</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {loading ? (
                    <TableSkeletonRows rows={8} cols={7} />
                  ) : rows.length === 0 ? (
                    <tr className="hover:bg-transparent">
                      <td colSpan={7}>
                        <EmptyState icon={Icon} title={isExit ? "No exits recorded" : "No entries recorded"} description="Records appear here as vehicles pass the cameras." />
                      </td>
                    </tr>
                  ) : (
                    rows.map((v) => {
                      const t = isExit ? v.exit_time : v.entry_time;
                      return (
                        <tr key={v.id}>
                          <td><VehiclePlate number={v.vehicle_number} /></td>
                          <td>
                            <div className="font-medium">{v.owner_name}</div>
                            <div className="text-xs text-muted-foreground">{v.vehicle_model || "—"}</div>
                          </td>
                          <td className="whitespace-nowrap text-muted-foreground">{v.contact_number || "—"}</td>
                          <td className="whitespace-nowrap text-muted-foreground">{formatDate(t)}</td>
                          <td className="whitespace-nowrap font-medium tabular-nums">
                            <span className="inline-flex items-center gap-1.5">
                              <Icon className={isExit ? "h-3.5 w-3.5 text-info" : "h-3.5 w-3.5 text-primary"} />
                              {formatTime(t)}
                            </span>
                          </td>
                          <td className="whitespace-nowrap tabular-nums text-muted-foreground">{elapsed(v.entry_time, v.exit_time)}</td>
                          <td><StatusBadge status={v.status} /></td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
            {!loading && (
              <Pagination
                page={page} pageSize={PAGE_SIZE} total={meta.total} count={rows.length}
                hasPrev={meta.hasPrev} hasNext={meta.hasNext}
                onPrev={() => setPage((p) => Math.max(1, p - 1))}
                onNext={() => setPage((p) => p + 1)}
                testIdPrefix={`${mode}-history`}
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}
