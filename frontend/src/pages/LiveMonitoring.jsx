import React from "react";
import { toast } from "sonner";
import PageHeader from "@/components/common/PageHeader";
import StatusBadge from "@/components/common/StatusBadge";
import PlateScanCard from "@/components/PlateScanCard";
import CameraGrid from "@/components/CameraGrid";
import useLiveEvents from "@/hooks/useLiveEvents";
import { useT } from "@/lib/i18n";
import { Radio, CarFront } from "lucide-react";

export default function LiveMonitoring() {
  const { t } = useT();
  // Toast the moment a plate is auto-recorded by any camera.
  const connected = useLiveEvents((msg) => {
    if (msg.type !== "entry.recorded") return;
    const plate = msg.vehicle_number || "—";
    const owner = msg.owner?.owner_name || (msg.owner_status === "new" ? t("live.newVisitor") : t("live.unknownOwner"));
    const cam = msg.camera_id || msg.session?.entry_camera || "";
    toast.success(`${plate} ${t("live.entered")}`, {
      description: `${owner}${cam ? ` · ${cam}` : ""}`,
      icon: <CarFront className="h-4 w-4 text-success" />,
      duration: 4500,
    });
  });

  return (
    <div className="space-y-6" data-testid="live-page">
      <PageHeader
        icon={Radio}
        title={t("live.title")}
        description={t("live.desc")}
        actions={
          <StatusBadge tone={connected ? "success" : "neutral"} dot={connected} className="px-3 py-1">
            {connected ? t("live.receiving") : t("live.connecting")}
          </StatusBadge>
        }
      />
      <CameraGrid />
      <PlateScanCard />
    </div>
  );
}
