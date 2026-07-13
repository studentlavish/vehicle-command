import React, { useEffect, useState } from "react";
import api from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { toast } from "sonner";import { Skeleton } from "@/components/ui/skeleton";
import { Building2, Camera, Database, ShieldCheck, MessageSquare, HardDrive, Save, Send, Loader2 } from "lucide-react";

export default function SettingsPage() {
  const [data, setData] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    (async () => {
      const { data } = await api.get("/settings");
      setData(data);
    })();
  }, []);

  const update = (k, v) => setData((d) => ({ ...d, [k]: v }));

  const save = async () => {
    setSaving(true);
    try {
      await api.put("/settings", data);
      toast.success("Settings saved");
    } catch (e) {
      toast.error("Save failed", { description: e.message });
    } finally {
      setSaving(false);
    }
  };

  if (!data) return <Skeleton className="h-96 bg-white/5 rounded-2xl" />;

  return (
    <div className="space-y-6" data-testid="settings-page">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <div className="inline-flex items-center gap-2 text-xs uppercase tracking-[0.25em] text-blue-400/80">
            <ShieldCheck className="h-3.5 w-3.5" /> System
          </div>
          <h1 className="mt-2 text-4xl md:text-5xl font-bold tracking-tight">Settings</h1>
          <p className="text-slate-400 mt-2 text-sm">Configure company details, integrations and data policies.</p>
        </div>
        <Button onClick={save} disabled={saving} className="bg-blue-600 hover:bg-blue-700" data-testid="save-settings-btn">
          <Save className="h-4 w-4 mr-2" /> {saving ? "Saving..." : "Save Changes"}
        </Button>
      </div>

      <Section icon={Building2} title="Company Details">
        <TextField label="Company Name" value={data.company_name} onChange={(v) => update("company_name", v)} test="setting-company-name" />
        <TextField label="Address" value={data.company_address} onChange={(v) => update("company_address", v)} test="setting-company-address" />
        <TextField label="Phone" value={data.company_phone} onChange={(v) => update("company_phone", v)} test="setting-company-phone" />
      </Section>

      <Section icon={Camera} title="Camera Settings">
        <TextField label="RTSP/HTTP Camera URL" value={data.camera_url} onChange={(v) => update("camera_url", v)} test="setting-camera-url" placeholder="rtsp://..." />
        <ToggleField label="Camera enabled" checked={!!data.camera_enabled} onChange={(v) => update("camera_enabled", v)} test="setting-camera-enabled" />
      </Section>

      <Section icon={Database} title="Database & Retention">
        <TextField label="Database URL" value={data.database_url_display} onChange={(v) => update("database_url_display", v)} test="setting-db-url" disabled />
        <NumberField label="Auto-delete after (days)" value={data.auto_delete_days} onChange={(v) => update("auto_delete_days", v)} test="setting-retention-days" />
        <p className="text-[11px] text-slate-500">Records older than the configured retention window are automatically purged. Default: 180 days.</p>
      </Section>

      <Section icon={MessageSquare} title="WhatsApp Report (Twilio)">
        <TextField label="Twilio Account SID" value={data.twilio_sid} onChange={(v) => update("twilio_sid", v)} test="setting-twilio-sid" placeholder="ACxxxxxxxxxxxxxx" />
        <TextField label="Twilio Auth Token" value={data.twilio_token} onChange={(v) => update("twilio_token", v)} test="setting-twilio-token" placeholder="****" />
        <TextField label="WhatsApp From" value={data.twilio_from} onChange={(v) => update("twilio_from", v)} test="setting-twilio-from" placeholder="whatsapp:+14155238886" />
        <TextField label="Admin WhatsApp To" value={data.twilio_to} onChange={(v) => update("twilio_to", v)} test="setting-twilio-to" placeholder="whatsapp:+91XXXXXXXXXX" />
        <TextField label="Daily Report Time (HH:MM)" value={data.whatsapp_report_time} onChange={(v) => update("whatsapp_report_time", v)} test="setting-whatsapp-time" placeholder="09:00" />
        <WhatsAppTester />
      </Section>

      <Section icon={HardDrive} title="Backup">
        <ToggleField label="Automatic daily backup" checked={!!data.backup_enabled} onChange={(v) => update("backup_enabled", v)} test="setting-backup-enabled" />
      </Section>
    </div>
  );
}

function Section({ icon: Icon, title, children }) {
  return (
    <div className="glass rounded-2xl p-6">
      <div className="flex items-center gap-3 mb-5">
        <div className="h-10 w-10 rounded-lg bg-blue-500/15 border border-blue-500/30 flex items-center justify-center">
          <Icon className="h-5 w-5 text-blue-400" />
        </div>
        <h3 className="font-semibold">{title}</h3>
      </div>
      <div className="grid gap-4 md:grid-cols-2">{children}</div>
    </div>
  );
}

function TextField({ label, value, onChange, test, placeholder, disabled }) {
  return (
    <div>
      <Label className="text-slate-300 text-xs uppercase tracking-widest">{label}</Label>
      <Input value={value ?? ""} disabled={disabled} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
        className="mt-2 bg-white/5 border-white/10 text-white" data-testid={test} />
    </div>
  );
}

function NumberField({ label, value, onChange, test }) {
  return (
    <div>
      <Label className="text-slate-300 text-xs uppercase tracking-widest">{label}</Label>
      <Input type="number" value={value ?? 0} onChange={(e) => onChange(Number(e.target.value))}
        className="mt-2 bg-white/5 border-white/10 text-white" data-testid={test} />
    </div>
  );
}

function ToggleField({ label, checked, onChange, test }) {
  return (
    <div className="flex items-center justify-between rounded-xl bg-white/5 border border-white/10 px-4 py-3">
      <div className="text-sm">{label}</div>
      <Switch checked={checked} onCheckedChange={onChange} data-testid={test} className="data-[state=checked]:bg-blue-600" />
    </div>
  );
}

function WhatsAppTester() {
  const [busy, setBusy] = React.useState(false);
  const [result, setResult] = React.useState(null);

  const send = async () => {
    setBusy(true);
    setResult(null);
    try {
      const { data } = await api.post("/whatsapp/send-report", {});
      setResult(data);
      if (data.delivered) {
        toast.success("WhatsApp sent", { description: `SID ${data.sid}` });
      } else {
        toast.info("Dry-run preview generated", { description: data.reason });
      }
    } catch (e) {
      toast.error("Send failed", { description: e.response?.data?.detail || e.message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="md:col-span-2 rounded-xl bg-white/5 border border-white/10 p-4 space-y-3" data-testid="whatsapp-tester">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-medium">Send Test Report Now</div>
          <div className="text-[11px] text-slate-500">Save Twilio credentials first, or trigger a dry-run preview.</div>
        </div>
        <Button onClick={send} disabled={busy} className="bg-blue-600 hover:bg-blue-700" data-testid="whatsapp-send-btn">
          {busy ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Send className="h-4 w-4 mr-2" />} Send
        </Button>
      </div>
      {result && (
        <div className="rounded-lg bg-[#0F172A]/60 border border-white/10 p-3">
          <div className="text-[10px] uppercase tracking-widest text-slate-400 mb-1">
            {result.delivered ? "Delivered" : `Preview (${result.mode})`}
          </div>
          <pre className="text-xs text-slate-200 whitespace-pre-wrap font-mono">{result.preview?.body}</pre>
          {result.reason && <div className="mt-2 text-[11px] text-amber-300">{result.reason}</div>}
        </div>
      )}
    </div>
  );
}
