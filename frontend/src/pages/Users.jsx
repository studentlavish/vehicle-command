import React, { useEffect, useState } from "react";
import api from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "sonner";
import { Skeleton } from "@/components/ui/skeleton";
import { Users, UserPlus, Trash2 } from "lucide-react";

const roleStyle = {
  admin: "bg-blue-500/15 text-blue-300 border-blue-500/30",
  manager: "bg-violet-500/15 text-violet-300 border-violet-500/30",
  security: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
};

function formatDate(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }); } catch { return iso; }
}

export default function UsersPage() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", email: "", role: "manager", status: "active" });

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/users");
      setRows(data);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const submit = async () => {
    try {
      await api.post("/users", form);
      toast.success("User added");
      setOpen(false);
      setForm({ name: "", email: "", role: "manager", status: "active" });
      load();
    } catch (e) {
      toast.error("Failed", { description: e.response?.data?.detail || e.message });
    }
  };

  const remove = async (u) => {
    if (!window.confirm(`Delete user ${u.name}?`)) return;
    try {
      await api.delete(`/users/${u.id}`);
      toast.success("User deleted");
      load();
    } catch (e) {
      toast.error("Delete failed", { description: e.response?.data?.detail || e.message });
    }
  };

  return (
    <div className="space-y-6" data-testid="users-page">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <div className="inline-flex items-center gap-2 text-xs uppercase tracking-[0.25em] text-blue-400/80">
            <Users className="h-3.5 w-3.5" /> Team
          </div>
          <h1 className="mt-2 text-4xl md:text-5xl font-bold tracking-tight">Users</h1>
          <p className="text-slate-400 mt-2 text-sm">Manage admins, managers and security personnel.</p>
        </div>
        <Button onClick={() => setOpen(true)} className="bg-blue-600 hover:bg-blue-700" data-testid="add-user-btn">
          <UserPlus className="h-4 w-4 mr-2" /> Add User
        </Button>
      </div>

      <div className="glass rounded-2xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-slate-400 text-[11px] uppercase tracking-widest border-b border-white/5">
                <th className="px-5 py-3 font-normal">Name</th>
                <th className="px-5 py-3 font-normal">Email</th>
                <th className="px-5 py-3 font-normal">Role</th>
                <th className="px-5 py-3 font-normal">Status</th>
                <th className="px-5 py-3 font-normal">Last Login</th>
                <th className="px-5 py-3 font-normal text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {loading ? (
                Array.from({ length: 4 }).map((_, i) => (
                  <tr key={`u-skel-${i}`}><td colSpan={6} className="p-3"><Skeleton className="h-8 bg-white/5" /></td></tr>
                ))
              ) : rows.map((u) => (
                <tr key={u.id} className="hover:bg-white/[0.03]" data-testid={`user-row-${u.email}`}>
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-3">
                      <div className="h-8 w-8 rounded-full bg-gradient-to-br from-blue-500 to-sky-500 flex items-center justify-center text-xs font-semibold">{u.name[0]}</div>
                      <div className="text-white">{u.name}</div>
                    </div>
                  </td>
                  <td className="px-5 py-3 text-slate-300">{u.email}</td>
                  <td className="px-5 py-3">
                    <Badge className={`${roleStyle[u.role] || "bg-white/5 text-slate-300 border-white/10"} border capitalize`}>{u.role}</Badge>
                  </td>
                  <td className="px-5 py-3">
                    {u.status === "active" ? (
                      <span className="text-emerald-300 pulse-dot text-sm">Active</span>
                    ) : (
                      <span className="text-slate-400 text-sm">Inactive</span>
                    )}
                  </td>
                  <td className="px-5 py-3 text-slate-300">{formatDate(u.last_login)}</td>
                  <td className="px-5 py-3 text-right">
                    <Button size="icon" variant="ghost" onClick={() => remove(u)} className="h-8 w-8 text-rose-400 hover:bg-rose-500/10" data-testid={`delete-user-${u.email}`}>
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="bg-[#0F172A]/95 border-white/10 text-white" data-testid="user-form-dialog">
          <DialogHeader><DialogTitle>Add User</DialogTitle></DialogHeader>
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2">
              <Label className="text-slate-300 text-xs uppercase tracking-widest">Name</Label>
              <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="mt-2 bg-white/5 border-white/10" data-testid="user-form-name" />
            </div>
            <div className="col-span-2">
              <Label className="text-slate-300 text-xs uppercase tracking-widest">Email</Label>
              <Input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} className="mt-2 bg-white/5 border-white/10" data-testid="user-form-email" />
            </div>
            <div>
              <Label className="text-slate-300 text-xs uppercase tracking-widest">Role</Label>
              <Select value={form.role} onValueChange={(role) => setForm({ ...form, role })}>
                <SelectTrigger className="mt-2 bg-white/5 border-white/10 text-white" data-testid="user-form-role"><SelectValue /></SelectTrigger>
                <SelectContent className="bg-[#0F172A]/95 border-white/10 text-white">
                  <SelectItem value="admin">Admin</SelectItem>
                  <SelectItem value="manager">Manager</SelectItem>
                  <SelectItem value="security">Security Guard</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="text-slate-300 text-xs uppercase tracking-widest">Status</Label>
              <Select value={form.status} onValueChange={(status) => setForm({ ...form, status })}>
                <SelectTrigger className="mt-2 bg-white/5 border-white/10 text-white" data-testid="user-form-status"><SelectValue /></SelectTrigger>
                <SelectContent className="bg-[#0F172A]/95 border-white/10 text-white">
                  <SelectItem value="active">Active</SelectItem>
                  <SelectItem value="inactive">Inactive</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)} className="text-slate-300">Cancel</Button>
            <Button onClick={submit} className="bg-blue-600 hover:bg-blue-700" data-testid="user-form-submit">Add User</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
