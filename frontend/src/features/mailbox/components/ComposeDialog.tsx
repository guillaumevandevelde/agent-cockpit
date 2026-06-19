import { useState } from "react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MarkdownPreviewToggle } from "@/components/shared/MarkdownPreviewToggle";
import { MODAL_SIZES } from "@/lib/constants";
import { mailApi } from "../api";
import { MESSAGE_KINDS, type Identity } from "../types";

const BROADCAST = "__broadcast__";

export function ComposeDialog({
  projectKey,
  identities,
  onClose,
  onSent,
}: {
  projectKey: string;
  identities: Identity[];
  onClose: () => void;
  onSent: () => void;
}) {
  const [to, setTo] = useState<string>(BROADCAST);
  const [kind, setKind] = useState<string>("note");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [sending, setSending] = useState(false);

  // The human always composes as the durable `human` handle.
  const recipients = identities.filter((i) => i.handle !== "human");

  const send = async () => {
    if (!subject.trim()) {
      toast.error("Subject is required");
      return;
    }
    setSending(true);
    try {
      await mailApi.send({
        project_key: projectKey,
        from_handle: "human",
        to_handle: to === BROADCAST ? null : to,
        kind,
        subject,
        body,
      });
      toast.success("Message sent");
      onSent();
      onClose();
    } catch {
      toast.error("Failed to send message");
    } finally {
      setSending(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className={MODAL_SIZES.MD}>
        <DialogHeader>
          <DialogTitle>Compose message</DialogTitle>
        </DialogHeader>

        <div className="flex flex-wrap gap-3">
          <div className="space-y-1">
            <Label className="text-xs">To</Label>
            <Select value={to} onValueChange={setTo}>
              <SelectTrigger className="h-8 w-[200px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={BROADCAST}>Broadcast (team)</SelectItem>
                {recipients.map((i) => (
                  <SelectItem key={i.handle} value={i.handle}>
                    {i.handle}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Kind</Label>
            <Select value={kind} onValueChange={setKind}>
              <SelectTrigger className="h-8 w-[180px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MESSAGE_KINDS.map((k) => (
                  <SelectItem key={k} value={k}>
                    {k}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="space-y-1">
          <Label className="text-xs">Subject</Label>
          <Input
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            placeholder="Subject"
          />
        </div>

        <div className="space-y-1">
          <Label className="text-xs">Body</Label>
          <MarkdownPreviewToggle value={body} onChange={setBody} minHeight="160px" />
        </div>

        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={send} disabled={sending}>
            Send
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
