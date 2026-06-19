import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { useProjectContext } from "@/contexts/ProjectContext";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { CLICKABLE_CARD } from "@/lib/constants";
import { kanbanApi } from "../kanban/api";
import { mailApi } from "./api";
import { ComposeDialog } from "./components/ComposeDialog";
import { MessageThread } from "./components/MessageThread";
import { MESSAGE_KINDS, type Identity, type Message } from "./types";

const ALL = "__all__";

export function MailboxPage() {
  const { activeProject } = useProjectContext();
  const projectPath = activeProject?.path ?? "";
  const [projectKey, setProjectKey] = useState("");
  const [identities, setIdentities] = useState<Identity[]>([]);
  const [handle, setHandle] = useState("human");
  const [kindFilter, setKindFilter] = useState<string>(ALL);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [composing, setComposing] = useState(false);
  const [threadRoot, setThreadRoot] = useState<string | null>(null);

  useEffect(() => {
    if (!projectPath) return;
    kanbanApi.projectKey(projectPath).then((r) => setProjectKey(r.project_key));
  }, [projectPath]);

  const reload = useCallback(async () => {
    if (!projectKey) return;
    try {
      const [idRes, inboxRes] = await Promise.all([
        mailApi.listIdentities(projectKey),
        mailApi.inbox(projectKey, handle, unreadOnly),
      ]);
      setIdentities(idRes.identities);
      setMessages(inboxRes.messages);
    } catch {
      toast.error("Failed to load mailbox");
    }
  }, [projectKey, handle, unreadOnly]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const handleOptions = useMemo(() => {
    const names = new Set(identities.map((i) => i.handle));
    names.add("human");
    return Array.from(names).sort();
  }, [identities]);

  const shown = useMemo(
    () => messages.filter((m) => kindFilter === ALL || m.kind === kindFilter),
    [messages, kindFilter]
  );

  const openMessage = async (m: Message) => {
    setThreadRoot(m.in_reply_to ?? m.id);
    if (m.status === "unread") {
      try {
        await mailApi.markRead(m.id, handle);
        void reload();
      } catch {
        // non-fatal: thread still opens
      }
    }
  };

  if (!projectPath) return <div className="p-6">Select a project first.</div>;

  return (
    <div className="space-y-4 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Agent Mail</h1>
          <p className="text-sm text-muted-foreground">
            Inter-agent messages, handoffs, and context requests.
          </p>
        </div>
        <Button onClick={() => setComposing(true)}>Compose</Button>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Select value={handle} onValueChange={setHandle}>
          <SelectTrigger className="h-8 w-[180px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {handleOptions.map((h) => (
              <SelectItem key={h} value={h}>
                Inbox: {h}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={kindFilter} onValueChange={setKindFilter}>
          <SelectTrigger className="h-8 w-[180px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All kinds</SelectItem>
            {MESSAGE_KINDS.map((k) => (
              <SelectItem key={k} value={k}>
                {k}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          size="sm"
          variant={unreadOnly ? "default" : "outline"}
          onClick={() => setUnreadOnly((v) => !v)}
        >
          Unread only
        </Button>
      </div>

      {shown.length === 0 && (
        <div className="text-sm text-muted-foreground">No messages.</div>
      )}

      <div className="space-y-2">
        {shown.map((m) => (
          <Card
            key={m.id}
            className={`${CLICKABLE_CARD} p-3`}
            role="button"
            tabIndex={0}
            onClick={() => openMessage(m)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                void openMessage(m);
              }
            }}
          >
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span className="font-medium text-foreground">{m.from_handle}</span>
              <span>→</span>
              <span>{m.to_handle ?? "team"}</span>
              <Badge variant="outline">{m.kind}</Badge>
              {m.status === "unread" && <Badge>unread</Badge>}
              {m.status === "answered" && <Badge variant="secondary">answered</Badge>}
              {m.card_id && <Badge variant="outline">card</Badge>}
              <span className="ml-auto">
                {new Date(m.created_at).toLocaleString()}
              </span>
            </div>
            <div className="mt-1 text-sm font-medium">{m.subject}</div>
          </Card>
        ))}
      </div>

      {composing && (
        <ComposeDialog
          projectKey={projectKey}
          identities={identities}
          onClose={() => setComposing(false)}
          onSent={reload}
        />
      )}
      {threadRoot && (
        <MessageThread rootId={threadRoot} onClose={() => setThreadRoot(null)} />
      )}
    </div>
  );
}
