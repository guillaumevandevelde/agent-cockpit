import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MODAL_SIZES } from "@/lib/constants";
import { cn } from "@/lib/utils";
import type { EndpointResponse, EndpointUpsertRequest } from "@/features/cc-bridge/types";

interface EndpointDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSave: (request: EndpointUpsertRequest) => Promise<void>;
  editEndpoint: EndpointResponse | null;
}

export function EndpointDialog({ open, onOpenChange, onSave, editEndpoint }: EndpointDialogProps) {
  const isEditing = !!editEndpoint;
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [credentialName, setCredentialName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    if (editEndpoint) {
      setName(editEndpoint.name);
      setBaseUrl(editEndpoint.base_url);
      setModel(editEndpoint.model);
      setCredentialName(editEndpoint.credential_name ?? "");
    } else {
      setName("");
      setBaseUrl("");
      setModel("");
      setCredentialName("");
    }
    setError(null);
  }, [open, editEndpoint]);

  const canSave = name.trim() && baseUrl.trim() && model.trim() && !submitting;

  async function handleSave() {
    if (!canSave) return;
    setError(null);
    setSubmitting(true);
    try {
      await onSave({
        name: name.trim(),
        base_url: baseUrl.trim(),
        model: model.trim(),
        credential_name: credentialName.trim() || null,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save endpoint");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={cn(MODAL_SIZES.SM)}>
        <DialogHeader>
          <DialogTitle>{isEditing ? "Edit endpoint" : "Add endpoint"}</DialogTitle>
          <DialogDescription>
            Register an Anthropic-compatible base URL + model pair for the subscription pool.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="endpoint-name">Name</Label>
            <Input
              id="endpoint-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="groq-free"
              autoComplete="off"
              disabled={isEditing}
            />
            {isEditing && (
              <p className="text-xs text-muted-foreground">
                Name is the lookup key; rename by deleting and re-adding.
              </p>
            )}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="endpoint-base-url">Base URL</Label>
            <Input
              id="endpoint-base-url"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="http://127.0.0.1:4000/v1"
              autoComplete="off"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="endpoint-model">Model</Label>
            <Input
              id="endpoint-model"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="groq/llama-3.3-70b-versatile"
              autoComplete="off"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="endpoint-credential">Credential name (optional)</Label>
            <Input
              id="endpoint-credential"
              value={credentialName}
              onChange={(e) => setCredentialName(e.target.value)}
              placeholder="groq_api_key"
              autoComplete="off"
            />
            <p className="text-xs text-muted-foreground">
              Leave empty for unauthenticated endpoints. Otherwise the name of a SecretStore entry.
            </p>
          </div>

          {error && (
            <div className="rounded-md bg-destructive/10 border border-destructive/20 px-3 py-2 text-sm text-destructive">
              {error}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={!canSave}>
            {submitting ? "Saving..." : isEditing ? "Update" : "Add endpoint"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}