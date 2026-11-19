import React, { useCallback, useEffect, useMemo, useState } from "react";
import api, { errorMessage } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "sonner";
import PageHeader from "@/components/common/PageHeader";
import StatusBadge from "@/components/common/StatusBadge";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import SecretRevealDialog from "@/components/common/SecretRevealDialog";
import { EmptyState, ErrorState } from "@/components/common/EmptyState";
import { TableSkeletonRows } from "@/components/common/Loaders";
import { Users, UserPlus, Trash2, Search, Loader2, Info, KeyRound } from "lucide-react";
import { formatDateTime, relativeAgo } from "@/lib/time";

const EMPTY = { name: "", email: "", role: "manager", status: "active" };

function Avatar({ name }) {
  const initials = (name || "?").split(/\s+/).map((s) => s[0]).join("").slice(0, 2).toUpperCase();
  return <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">{initials}</div>;
}

export default function UsersPage() {
  const { user: me } = useAuth();
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [formErr, setFormErr] = useState({});
  const [saving, setSaving] = useState(false);
  const [toDelete, setToDelete] = useState(null);
  const [toReset, setToReset] = useState(null);
  const [revealed, setRevealed] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const { data } = await api.get("/users");
      setRows(Array.isArray(data) ? data : []);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const shown = useMemo(() => {
    const f = filter.trim().toLowerCase();
    if (!f) return rows;
    return rows.filter((u) => `${u.name} ${u.email} ${u.role}`.toLowerCase().includes(f));
  }, [rows, filter]);

  const submit = async (e) => {
    e.preventDefault();
    const errs = {};
    if (!form.name.trim()) errs.name = "Name is required";
    if (!/^\S+@\S+\.\S+$/.test(form.email.trim())) errs.email = "Enter a valid email address";
    setFormErr(errs);
    if (Object.keys(errs).length) return;
    setSaving(true);
    try {
      const { data } = await api.post("/users", { ...form, name: form.name.trim(), email: form.email.trim() });
      toast.success("User added", { description: form.email });
      if (data?.temporary_password) setRevealed({ label: data.email, value: data.temporary_password });
      setOpen(false);
      setForm(EMPTY);
      load();
    } catch (err) {
      toast.error("Couldn't add user", { description: errorMessage(err) });
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    try {
      await api.delete(`/users/${toDelete.id}`);
      toast.success("User removed", { description: toDelete.email });
      load();
    } catch (err) {
      toast.error("Couldn't remove user", { description: errorMessage(err) });
    }
  };

  const resetPassword = async () => {
    try {
      const { data } = await api.post(`/users/${toReset.id}/password`, {});
      setRevealed({ label: toReset.email, value: data.temporary_password });
    } catch (err) {
      toast.error("Couldn't reset password", { description: errorMessage(err) });
    }
  };

  const canManage = me?.role === "admin";

  return (
    <div className="space-y-6" data-testid="users-page">
      <PageHeader
        icon={Users}
        title="Users"
        description="Admins, managers and security staff who can access the system."
        actions={canManage && (
          <Button onClick={() => { setForm(EMPTY); setFormErr({}); setOpen(true); }} data-testid="add-user-btn">
            <UserPlus className="h-4 w-4" /> Add user
          </Button>
        )}
      />

      <div className="surface overflow-hidden">
        <div className="flex flex-col gap-3 border-b p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="relative w-full sm:max-w-xs">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter by name, email or role" className="pl-9" />
          </div>
          {!loading && <div className="text-sm text-muted-foreground">{rows.length} user{rows.length === 1 ? "" : "s"}</div>}
        </div>

        {error ? (
          <ErrorState onRetry={load} />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>User</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Last sign-in</th>
                  {canManage && <th className="text-right">Actions</th>}
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <TableSkeletonRows rows={4} cols={canManage ? 5 : 4} />
                ) : shown.length === 0 ? (
                  <tr className="hover:bg-transparent">
                    <td colSpan={5}><EmptyState icon={Users} title={filter ? "No users match" : "No users yet"} /></td>
                  </tr>
                ) : (
                  shown.map((u) => {
                    const isMe = me?.id === u.id || me?.email === u.email;
                    return (
                      <tr key={u.id} data-testid={`user-row-${u.email}`}>
                        <td>
                          <div className="flex items-center gap-3">
                            <Avatar name={u.name} />
                            <div className="min-w-0">
                              <div className="flex items-center gap-2 font-medium">
                                {u.name}
                                {isMe && <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">You</span>}
                              </div>
                              <div className="truncate text-xs text-muted-foreground">{u.email}</div>
                            </div>
                          </div>
                        </td>
                        <td><StatusBadge status={u.role} /></td>
                        <td><StatusBadge tone={u.status === "active" ? "success" : "neutral"} dot={u.status === "active"}>{u.status === "active" ? "Active" : "Inactive"}</StatusBadge></td>
                        <td className="whitespace-nowrap text-muted-foreground" title={formatDateTime(u.last_login)}>
                          {u.last_login ? relativeAgo(u.last_login) : "Never"}
                        </td>
                        {canManage && (
                          <td className="whitespace-nowrap text-right">
                            {!isMe && (
                              <Button size="icon-sm" variant="ghost" onClick={() => setToReset(u)} aria-label={`Reset password for ${u.name}`} title="Reset password" data-testid={`reset-password-${u.email}`}>
                                <KeyRound />
                              </Button>
                            )}
                            {!isMe && (
                              <Button size="icon-sm" variant="ghost-danger" onClick={() => setToDelete(u)} aria-label={`Remove ${u.name}`} data-testid={`delete-user-${u.email}`}>
                                <Trash2 />
                              </Button>
                            )}
                          </td>
                        )}
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <Dialog open={open} onOpenChange={(v) => !saving && setOpen(v)}>
        <DialogContent data-testid="user-form-dialog">
          <form onSubmit={submit} className="space-y-5">
            <DialogHeader>
              <div className="flex items-center gap-3">
                <div className="icon-tile"><UserPlus className="h-5 w-5" /></div>
                <div className="text-left">
                  <DialogTitle>Add user</DialogTitle>
                  <DialogDescription>Give a team member access to the dashboard.</DialogDescription>
                </div>
              </div>
            </DialogHeader>
            <div className="grid grid-cols-2 gap-4">
              <div className="col-span-2">
                <label className="field-label">Full name <span className="text-destructive">*</span></label>
                <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={formErr.name ? "border-destructive" : ""} data-testid="user-form-name" autoFocus />
                {formErr.name && <p className="mt-1 text-xs text-destructive">{formErr.name}</p>}
              </div>
              <div className="col-span-2">
                <label className="field-label">Email <span className="text-destructive">*</span></label>
                <Input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="name@company.com" className={formErr.email ? "border-destructive" : ""} data-testid="user-form-email" />
                {formErr.email && <p className="mt-1 text-xs text-destructive">{formErr.email}</p>}
              </div>
              <div>
                <label className="field-label">Role</label>
                <Select value={form.role} onValueChange={(role) => setForm({ ...form, role })}>
                  <SelectTrigger data-testid="user-form-role"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="admin">Admin</SelectItem>
                    <SelectItem value="manager">Manager</SelectItem>
                    <SelectItem value="security">Security</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="field-label">Status</label>
                <Select value={form.status} onValueChange={(status) => setForm({ ...form, status })}>
                  <SelectTrigger data-testid="user-form-status"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="active">Active</SelectItem>
                    <SelectItem value="inactive">Inactive</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="flex items-start gap-2 rounded-lg bg-info/10 px-3 py-2.5 text-xs text-info">
              <Info className="mt-px h-3.5 w-3.5 shrink-0" />
              A temporary password is shown after you add the user. They can also sign in with Google using this email.
            </div>
            <DialogFooter className="gap-2">
              <Button type="button" variant="outline" onClick={() => setOpen(false)} disabled={saving}>Cancel</Button>
              <Button type="submit" disabled={saving} data-testid="user-form-submit">
                {saving && <Loader2 className="h-4 w-4 animate-spin" />} Add user
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(v) => !v && setToDelete(null)}
        title={`Remove ${toDelete?.name || "user"}?`}
        description="They will lose access to the dashboard immediately."
        confirmLabel="Remove user"
        onConfirm={remove}
      />

      <ConfirmDialog
        open={!!toReset}
        onOpenChange={(v) => !v && setToReset(null)}
        title={`Reset password for ${toReset?.name || "user"}?`}
        description="Their current password stops working. You'll see a new temporary password to give them."
        confirmLabel="Reset password"
        destructive={false}
        onConfirm={resetPassword}
      />

      <SecretRevealDialog
        secret={revealed}
        title="Temporary password"
        description="Give this to the user. They should change it after signing in (menu → Change password)."
        onClose={() => setRevealed(null)}
      />
    </div>
  );
}
