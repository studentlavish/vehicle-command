import React from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/common/EmptyState";
import { MapPinOff } from "lucide-react";

export default function NotFound() {
  return (
    <div className="surface">
      <EmptyState
        icon={MapPinOff}
        title="Page not found"
        description="The page you're looking for doesn't exist or has moved."
        action={<Button asChild><Link to="/">Back to dashboard</Link></Button>}
      />
    </div>
  );
}
