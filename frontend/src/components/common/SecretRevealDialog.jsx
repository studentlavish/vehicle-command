import React, { useState } from "react";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Check, Copy, KeyRound } from "lucide-react";

/**
 * Shows a password or secret exactly once, with a copy button.
 * The server never returns it again, so the copy is the only chance to save it.
 */
export default function SecretRevealDialog({ secret, title, description, onClose }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(secret?.value || "");
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };
  return (
    <Dialog open={!!secret} onOpenChange={(v) => !v && onClose()}>
      <DialogContent data-testid="secret-reveal-dialog">
        <DialogHeader>
          <div className="flex items-center gap-3">
            <div className="icon-tile"><KeyRound className="h-5 w-5" /></div>
            <div className="text-left">
              <DialogTitle>{title}</DialogTitle>
              <DialogDescription>{description}</DialogDescription>
            </div>
          </div>
        </DialogHeader>
        {secret?.label && <div className="text-sm text-muted-foreground">{secret.label}</div>}
        <div className="flex items-center gap-2">
          <code className="flex-1 select-all break-all rounded-lg border bg-muted px-3 py-2.5 font-mono text-sm" data-testid="secret-value">
            {secret?.value}
          </code>
          <Button type="button" variant="outline" size="icon" onClick={copy} aria-label="Copy">
            {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
          </Button>
        </div>
        <p className="text-xs text-warning">This is shown only once. Copy it now — it can't be viewed again.</p>
        <DialogFooter>
          <Button type="button" onClick={onClose} data-testid="secret-reveal-done">Done</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
