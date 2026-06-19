import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { MarkdownRenderer } from "@/components/shared/MarkdownRenderer";
import { MODAL_SIZES } from "@/lib/constants";
import { mailApi } from "../api";
import type { Message } from "../types";

export function MessageThread({
  rootId,
  onClose,
}: {
  rootId: string;
  onClose: () => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);

  useEffect(() => {
    mailApi
      .thread(rootId)
      .then((r) => setMessages(r.messages))
      .catch(() => setMessages([]));
  }, [rootId]);

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className={MODAL_SIZES.MD}>
        <DialogHeader>
          <DialogTitle>Thread</DialogTitle>
        </DialogHeader>
        <div className="space-y-4 overflow-y-auto">
          {messages.map((m) => (
            <div key={m.id} className="rounded-md border p-3">
              <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span className="font-medium text-foreground">{m.from_handle}</span>
                <span>→</span>
                <span>{m.to_handle ?? "team"}</span>
                <Badge variant="outline">{m.kind}</Badge>
                <Badge variant="secondary">{m.status}</Badge>
                <span className="ml-auto">
                  {new Date(m.created_at).toLocaleString()}
                </span>
              </div>
              <div className="mb-1 text-sm font-medium">{m.subject}</div>
              <div className="text-sm">
                <MarkdownRenderer content={m.body || "_No body_"} />
              </div>
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
