import React, { useCallback, useEffect, useMemo, useState } from "react";
import api, { errorMessage } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import PageHeader from "@/components/common/PageHeader";
import SectionCard from "@/components/common/SectionCard";
import AgentManager from "@/components/AgentManager";
import CameraRoles from "@/components/CameraRoles";
import { ErrorState } from "@/components/common/EmptyState";
import {
  Building2, Camera, Database, Settings as SettingsIcon, MessageSquare, HardDrive,
  Save, Send, Loader2, Eye, EyeOff, RotateCcw, CheckCircle2, Info,
} from "lucide-react";

export default function SettingsPage() {
  const [data, setData] = useState(null);
  const [initial, setInitial] = useState(null);
  const [error, setError] = useState(false);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setError(false);
    try {
      const { data } = await api.get("/settings");
      setData(data);
      setInitial(data);
    } catch {
      setError(true);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const dirty = useMemo(() => JSON.stringify(data) !== JSON.stringify(initial), [data, initial]);
  const update = (k, v) => setData((d) => ({ ...d, [k]: v }));

  const save = async () => {
    setSaving(true);
    try {
      await api.put("/settings", data);
      setInitial(data);
      toast.success("Settings saved");
    } catch (e) {
      toast.error("Couldn't save settings", { description: errorMessage(e) });
    } finally {
      setSaving(false);
    }
  };

  const header = (
    <PageHeader
      icon={SettingsIcon}
      title="Settings"
      description="Company details, cameras, integrations and data policies."
      actions={data && (
        <>
          {dirty && (
            <Button variant="ghost" onClick={() => setData(initial)} disabled={saving}>
              <RotateCcw className="h-4 w-4" /> Discard
            </Button>
          )}
          <Button onClick={save} disabled={saving || !dirty} data-testid="save-settings-btn">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            {saving ? "Saving…" : "Save changes"}
          </Button>
        </>
      )}
    />
  );

  if (error) return <div className="space-y-6">{header}<div className="surface"><ErrorState onRetry={load} /></div></div>;
  if (!data) {
    return (
      <div className="space-y-6">
        {header}
        {[0, 1, 2].map((i) => <Skeleton key={i} className="h-48 rounded-xl" />)}
      </div>
    );
  }

  return (
    <div className="space-y-6 pb-20" data-testid="settings-page">
      {header}

      <SectionCard icon={Building2} title="Company details" description="Shown on reports and notifications">
        <div className="grid gap-4 md:grid-cols-2">
          <TextField label="Company name" value={data.company_name} onChange={(v) => update("company_name", v)} test="setting-company-name" />
          <TextField label="Phone" value={data.company_phone} onChange={(v) => update("company_phone", v)} test="setting-company-phone" />
          <TextField className="md:col-span-2" label="Address" value={data.company_address} onChange={(v) => update("company_address", v)} test="setting-company-address" />
        </div>
      </SectionCard>

      <SectionCard icon={Camera} title="Camera" description="Default camera stream">
        <div className="grid gap-4 md:grid-cols-2">
          <TextField label="RTSP / HTTP camera URL" value={data.camera_url} onChange={(v) => update("camera_url", v)} test="setting-camera-url" placeholder="rtsp://user:pass@192.168.1.10:554/..." mono />
          <ToggleField label="Camera enabled" description="Capture frames from this URL" checked={!!data.camera_enabled} onChange={(v) => update("camera_enabled", v)} test="setting-camera-enabled" />
        </div>
      </SectionCard>

      <SectionCard icon={Database} title="Database & retention">
        <div className="grid gap-4 md:grid-cols-2">
          <TextField label="Database" value={data.database_url_display} onChange={() => {}} test="setting-db-url" disabled mono />
          <TextField type="number" label="Auto-delete records after (days)" value={data.auto_delete_days} onChange={(v) => update("auto_delete_days", Number(v))} test="setting-retention-days" />
        </div>
        <Hint>Visit records older than the retention window are removed automatically. Default: 180 days.</Hint>
      </SectionCard>

      <SectionCard icon={MessageSquare} title="WhatsApp report (Twilio)" description="Daily summary sent to the admin's WhatsApp">
        <div className="grid gap-4 md:grid-cols-2">
          <TextField label="Account SID" value={data.twilio_sid} onChange={(v) => update("twilio_sid", v)} test="setting-twilio-sid" placeholder="ACxxxxxxxxxxxxxxxx" mono />
          <TextField label="Auth token" secret value={data.twilio_token} onChange={(v) => update("twilio_token", v)} test="setting-twilio-token" placeholder={data.twilio_token_set ? "Saved — leave blank to keep" : "Paste auth token"} mono />
          <TextField label="WhatsApp from" value={data.twilio_from} onChange={(v) => update("twilio_from", v)} test="setting-twilio-from" placeholder="whatsapp:+14155238886" mono />
          <TextField label="Admin WhatsApp to" value={data.twilio_to} onChange={(v) => update("twilio_to", v)} test="setting-twilio-to" placeholder="whatsapp:+91XXXXXXXXXX" mono />
          <TextField type="time" label="Daily report time" value={data.whatsapp_report_time} onChange={(v) => update("whatsapp_report_time", v)} test="setting-whatsapp-time" />
        </div>
        <WhatsAppTester dirty={dirty} />
      </SectionCard>

      <CameraRoles />

      <AgentManager />

      <SectionCard icon={HardDrive} title="Backup">
        <ToggleField label="Automatic daily backup" description="Database snapshot every night" checked={!!data.backup_enabled} onChange={(v) => update("backup_enabled", v)} test="setting-backup-enabled" />
      </SectionCard>

      {dirty && (
        <div className="fixed inset-x-0 bottom-4 z-30 flex justify-center px-4 lg:pl-64">
          <div className="flex items-center gap-3 rounded-xl border bg-card px-4 py-2.5 shadow-pop animate-fade-in">
            <span className="text-sm">You have unsaved changes</span>
            <Button size="sm" variant="ghost" onClick={() => setData(initial)} disabled={saving}>Discard</Button>
            <Button size="sm" onClick={save} disabled={saving}>
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} Save
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

function Hint({ children }) {
  return (
    <p className="mt-4 flex items-start gap-2 text-xs text-muted-foreground">
      <Info className="mt-px h-3.5 w-3.5 shrink-0" /> {children}
    </p>
  );
}

function TextField({ label, value, onChange, test, placeholder, disabled, secret, mono, type = "text", className }) {
  const [show, setShow] = useState(false);
  return (
    <div className={className}>
      <label className="field-label">{label}</label>
      <div className="relative">
        <Input
          type={secret && !show ? "password" : type}
          value={value ?? ""}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className={`${mono ? "font-mono text-[13px]" : ""} ${secret ? "pr-10" : ""}`}
          data-testid={test}
        />
        {secret && (
          <button type="button" onClick={() => setShow((s) => !s)} className="absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={show ? "Hide" : "Show"}>
            {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </button>
        )}
      </div>
    </div>
  );
}

function ToggleField({ label, description, checked, onChange, test }) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-4 rounded-lg border px-4 py-3 transition-colors hover:bg-muted/40 md:self-end">
      <div>
        <div className="text-sm font-medium">{label}</div>
        {description ? <div className="text-xs text-muted-foreground">{description}</div> : null}
      </div>
      <Switch checked={checked} onCheckedChange={onChange} data-testid={test} />
    </label>
  );
}

function WhatsAppTester({ dirty }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);

  const send = async () => {
    setBusy(true);
    setResult(null);
    try {
      const { data } = await api.post("/whatsapp/send-report", {});
      setResult(data);
      if (data.delivered) toast.success("WhatsApp report sent", { description: `Message SID ${data.sid}` });
      else toast.info("Preview generated (not sent)", { description: data.reason });
    } catch (e) {
      toast.error("Send failed", { description: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-4 rounded-lg border bg-muted/40 p-4" data-testid="whatsapp-tester">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="text-sm font-medium">Send a test report</div>
          <div className="text-xs text-muted-foreground">
            {dirty ? "Save your changes first so the test uses the new credentials." : "Without credentials you'll get a preview instead."}
          </div>
        </div>
        <Button variant="outline" onClick={send} disabled={busy || dirty} data-testid="whatsapp-send-btn">
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />} Send test
        </Button>
      </div>
      {result && (
        <div className="mt-4 rounded-lg border bg-card p-3">
          <div className="mb-2 flex items-center gap-1.5 text-xs font-medium">
            {result.delivered ? <CheckCircle2 className="h-3.5 w-3.5 text-success" /> : <Info className="h-3.5 w-3.5 text-info" />}
            {result.delivered ? "Delivered" : `Preview (${result.mode})`}
          </div>
          <pre className="whitespace-pre-wrap font-mono text-xs text-foreground/80">{result.preview?.body}</pre>
          {result.reason && <div className="mt-2 text-xs text-warning">{result.reason}</div>}
        </div>
      )}
    </div>
  );
}
