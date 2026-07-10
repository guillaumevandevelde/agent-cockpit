import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { MarkdownPreviewToggle } from "@/components/shared/MarkdownPreviewToggle";
import { MODAL_SIZES } from "@/lib/constants";
import { cn } from "@/lib/utils";
import { formatTimestamp } from "@/features/usage/utils";
import { fetchResumableSessions } from "@/features/cc-bridge/api";
import { useProviderContext } from "@/contexts/ProviderContext";
import { PRIORITIES, WORK_TYPES, DEFAULT_MODEL_SUGGESTIONS, type Priority, type WorkType } from "../types";
import { kanbanApi } from "../api";
import type { ResumableSession } from "@/types/sessions";

function parseLabels(raw: string): string[] {
  return raw
    .split(",")
    .map((l) => l.trim())
    .filter(Boolean);
}

const AUTO = "__auto__"; // sentinel: null agent (dispatch resolves the provider at run time)
const NO_WORK_TYPE = ""; // sentinel: no work_type set (routing hint is purely optional)

/** ISO datetime -> local "YYYY-MM-DDTHH:mm" for a native datetime-local input. */
function toDatetimeLocalValue(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function CardEditDialog({
  open,
  initial,
  defaultAgent,
  projectPath,
  onClose,
  onSubmit,
}: {
  open: boolean;
  initial?: {
    title: string;
    description: string;
    priority?: string | null;
    labels?: string[] | null;
    work_type?: string | null;
    model?: string | null;
    transport?: string | null;
    resume_session_id?: string | null;
    resume_project_folder?: string | null;
    scheduled_at?: string | null;
    analyst_agent_id?: string | null;
    executor_agent_id?: string | null;
  };
  defaultAgent?: string | null;
  projectPath?: string;
  onClose: () => void;
  onSubmit: (data: {
    title: string;
    description: string;
    priority: string | null;
    labels: string[];
    work_type: string | null;
    agent: string | null;
    model: string | null;
    transport: string | null;
    resume_session_id: string | null;
    resume_project_folder: string | null;
    scheduled_at: string | null;
    analyst_agent_id: string | null;
    executor_agent_id: string | null;
  }) => void;
}) {
  const [title, setTitle] = useState(initial?.title ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [priority, setPriority] = useState<Priority>(
    (initial?.priority as Priority) ?? "none"
  );
  const [labelsInput, setLabelsInput] = useState(
    (initial?.labels ?? []).join(", ")
  );
  const [workType, setWorkType] = useState<WorkType | "">(
    (initial?.work_type as WorkType) ?? ""
  );
  const [agent, setAgent] = useState<string>(defaultAgent ?? AUTO);
  const [model, setModel] = useState<string>(initial?.model ?? "");
  const [modelOptions, setModelOptions] = useState<string[]>([...DEFAULT_MODEL_SUGGESTIONS]);
  const [analystAgentId, setAnalystAgentId] = useState<string>(initial?.analyst_agent_id ?? AUTO);
  const [executorAgentId, setExecutorAgentId] = useState<string>(initial?.executor_agent_id ?? AUTO);
  const [transport, setTransport] = useState<string>(initial?.transport ?? "auto");
  const [scheduledAt, setScheduledAt] = useState<string>(
    initial?.scheduled_at ? toDatetimeLocalValue(initial.scheduled_at) : ""
  );
  const { providers } = useProviderContext();
  const installedProviders = providers.filter((p) => p.installed);

  const [resumeSessions, setResumeSessions] = useState<ResumableSession[]>([]);
  const [selectedResume, setSelectedResume] = useState<ResumableSession | null>(null);
  const [loadingResume, setLoadingResume] = useState(false);
  const [showResumePicker, setShowResumePicker] = useState(
    !!initial?.resume_session_id
  );
  // Tracks whether the user has ever interacted with the resume picker.
  // Until they do, we preserve the initial value on submit.
  const [resumeTouched, setResumeTouched] = useState(false);

  const labels = parseLabels(labelsInput);

  useEffect(() => {
    if (!open) return;
    kanbanApi.getModelOptions()
      .then((r) => { if (Array.isArray(r?.options)) setModelOptions(r.options); })
      .catch(() => {});
  }, [open]);

  // Pre-select the existing resume session when editing
  useEffect(() => {
    if (initial?.resume_session_id && resumeSessions.length > 0) {
      const match = resumeSessions.find((s) => s.id === initial.resume_session_id);
      if (match) setSelectedResume(match);
    }
  }, [initial?.resume_session_id, resumeSessions]);

  // Fetch resumable sessions when the picker is opened
  useEffect(() => {
    if (!showResumePicker || !projectPath) return;
    let cancelled = false;
    setLoadingResume(true);
    setResumeSessions([]);
    fetchResumableSessions(projectPath, 30)
      .then((r) => { if (!cancelled) setResumeSessions(r.sessions); })
      .catch(() => { if (!cancelled) setResumeSessions([]); })
      .finally(() => { if (!cancelled) setLoadingResume(false); });
    return () => { cancelled = true; };
  }, [showResumePicker, projectPath]);

  // If the user has interacted with the picker, use their selection; otherwise
  // preserve the initial value so editing an existing card without touching the
  // picker doesn't silently clear a pre-existing resume association.
  const resume_session_id = resumeTouched
    ? (selectedResume?.id ?? null)
    : (selectedResume?.id ?? initial?.resume_session_id ?? null);
  const resume_project_folder = resumeTouched
    ? (selectedResume?.project_folder ?? null)
    : (selectedResume?.project_folder ?? initial?.resume_project_folder ?? null);

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className={MODAL_SIZES.MD}>
        <DialogHeader>
          <DialogTitle>{initial ? "Edit card" : "New card"}</DialogTitle>
          <DialogDescription>
            {initial ? "Update the card details below." : "Create a new card for your kanban board."}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="card-work-type">Work type</Label>
            <Select
              value={workType || NO_WORK_TYPE}
              onValueChange={(v) => setWorkType(v === NO_WORK_TYPE ? "" : (v as WorkType))}
            >
              <SelectTrigger id="card-work-type">
                <SelectValue placeholder="(unset — dispatcher falls back)" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NO_WORK_TYPE}>(unset)</SelectItem>
                {WORK_TYPES.map((w) => (
                  <SelectItem key={w} value={w}>
                    {w}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              Structured routing hint used by auto-dispatch (analysis → analyst,
              feature/bug/chore → engineer). Free-form labels below are unaffected.
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="card-title">Title</Label>
            <Input
              id="card-title"
              placeholder="Enter card title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>

          <div className="space-y-2">
            <Label>Description</Label>
            <MarkdownPreviewToggle value={description} onChange={setDescription} minHeight="140px" />
          </div>

          <div className="space-y-2">
            <Label>Priority</Label>
            <Select value={priority} onValueChange={(v) => setPriority(v as Priority)}>
              <SelectTrigger>
                <SelectValue placeholder="Select priority" />
              </SelectTrigger>
              <SelectContent>
                {PRIORITIES.map((p) => (
                  <SelectItem key={p} value={p}>
                    {p}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label>Provider</Label>
            <Select value={agent} onValueChange={setAgent}>
              <SelectTrigger>
                <SelectValue placeholder="Provider" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={AUTO}>Auto (selected provider)</SelectItem>
                {installedProviders.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="card-model">Model</Label>
            <input
              id="card-model"
              list="card-model-suggestions"
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
              placeholder="(unset — falls back to column/persona default)"
              value={model}
              onChange={(e) => setModel(e.target.value)}
            />
            <datalist id="card-model-suggestions">
              {modelOptions.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
            <p className="text-xs text-muted-foreground">
              Overrides the column default and persona frontmatter for this card only.
            </p>
          </div>

          <div className="space-y-1">
            <Label htmlFor="analyst_agent_id">Analyst-agent (multi-agent split)</Label>
            <Select value={analystAgentId}
                    onValueChange={(v) => setAnalystAgentId(v === AUTO ? AUTO : v)}>
              <SelectTrigger id="analyst_agent_id">
                <SelectValue placeholder="Geen (single-agent)" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={AUTO}>Geen (single-agent)</SelectItem>
                <SelectItem value="claude-code">Claude Code</SelectItem>
                <SelectItem value="mimo-code">MiniMax (mimo-code)</SelectItem>
                <SelectItem value="codex-cli">Codex CLI</SelectItem>
                <SelectItem value="open-code">OpenCode</SelectItem>
                <SelectItem value="copilot-cli">Copilot CLI</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1">
            <Label htmlFor="executor_agent_id">Executor-agent</Label>
            <Select value={executorAgentId}
                    onValueChange={(v) => setExecutorAgentId(v === AUTO ? AUTO : v)}>
              <SelectTrigger id="executor_agent_id">
                <SelectValue placeholder="Auto (= card.agent)" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={AUTO}>Auto (= card.agent)</SelectItem>
                <SelectItem value="claude-code">Claude Code</SelectItem>
                <SelectItem value="mimo-code">MiniMax (mimo-code)</SelectItem>
                <SelectItem value="codex-cli">Codex CLI</SelectItem>
                <SelectItem value="open-code">OpenCode</SelectItem>
                <SelectItem value="copilot-cli">Copilot CLI</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label>Transport</Label>
            <Select value={transport} onValueChange={setTransport}>
              <SelectTrigger>
                <SelectValue placeholder="Select transport" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="auto">Auto (use project default)</SelectItem>
                <SelectItem value="worktree">Worktree (local)</SelectItem>
                <SelectItem value="sandcastle">Sandcastle</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              {transport === "auto"
                ? "Uses project's sandcastle config if enabled, otherwise worktree."
                : transport === "sandcastle"
                ? "Run via sandcastle. Isolation depends on the project's sandbox provider (docker/podman/no-sandbox)."
                : "Run this card locally with git worktree."}
            </p>
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label htmlFor="card-scheduled-at">Schedule for</Label>
              {scheduledAt && (
                <button
                  type="button"
                  className="text-xs text-muted-foreground hover:text-destructive"
                  onClick={() => setScheduledAt("")}
                >
                  Clear
                </button>
              )}
            </div>
            <Input
              id="card-scheduled-at"
              type="datetime-local"
              value={scheduledAt}
              onChange={(e) => setScheduledAt(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              {scheduledAt
                ? "Auto-dispatch won't pick up this card until the scheduled time."
                : "Optional — leave empty to make the card available immediately."}
            </p>
          </div>

          {/* Resume session picker */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label>Resume previous session</Label>
              <div className="flex items-center gap-2">
                {resume_session_id && (
                  <button
                    type="button"
                    className="text-xs text-muted-foreground hover:text-destructive"
                    onClick={() => { setSelectedResume(null); setResumeTouched(true); setShowResumePicker(false); }}
                  >
                    Clear
                  </button>
                )}
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-6 text-xs"
                  onClick={() => setShowResumePicker((v) => !v)}
                >
                  {showResumePicker ? "Close" : "Pick session"}
                </Button>
              </div>
            </div>

            {showResumePicker && (
              <div className="space-y-1.5">
                {!projectPath ? (
                  <p className="text-xs text-muted-foreground py-2">
                    No project selected — save the card first, then edit it with a project active.
                  </p>
                ) : loadingResume ? (
                  <div className="flex items-center justify-center py-6 text-sm text-muted-foreground">
                    Loading sessions…
                  </div>
                ) : resumeSessions.length === 0 ? (
                  <div className="flex items-center justify-center py-6 text-sm text-muted-foreground">
                    No recent sessions found for this project.
                  </div>
                ) : (
                  <div className="max-h-48 overflow-y-auto rounded-md border">
                    {resumeSessions.map((session) => (
                      <button
                        key={session.id}
                        type="button"
                        className={cn(
                          "block w-full min-w-0 text-left px-3 py-2 border-b last:border-b-0 transition-colors",
                          selectedResume?.id === session.id
                            ? "border-l-2 border-l-primary bg-primary/5"
                            : "hover:bg-muted/50"
                        )}
                        onClick={() => {
                          setSelectedResume((prev) =>
                            prev?.id === session.id ? null : session
                          );
                          setResumeTouched(true);
                        }}
                      >
                        <div className="flex items-center justify-between gap-2 min-w-0">
                          <span className="flex items-center gap-1.5 min-w-0">
                            <span className="text-sm font-medium truncate min-w-0">
                              {session.project_name}
                            </span>
                            <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                              {session.worktree_label}
                            </span>
                          </span>
                          <span className="text-xs text-muted-foreground shrink-0">
                            {formatTimestamp(session.modified_at)}
                          </span>
                        </div>
                        {session.summary && (
                          <p className="text-xs text-muted-foreground mt-0.5 truncate">
                            {session.summary}
                          </p>
                        )}
                      </button>
                    ))}
                  </div>
                )}
                {selectedResume && (
                  <p className="text-xs text-muted-foreground">
                    Will resume: <span className="font-medium">{selectedResume.project_name}</span>{" "}
                    ({selectedResume.worktree_label})
                  </p>
                )}
              </div>
            )}

            {!showResumePicker && resume_session_id && (
              <p className="text-xs text-muted-foreground">
                Resuming session <span className="font-mono">{resume_session_id.slice(0, 8)}…</span>
              </p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="card-labels">Labels</Label>
            <Input
              id="card-labels"
              placeholder="comma, separated, labels"
              value={labelsInput}
              onChange={(e) => setLabelsInput(e.target.value)}
            />
            {labels.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {labels.map((l) => (
                  <Badge key={l} variant="outline" className="text-[10px] font-normal">
                    {l}
                  </Badge>
                ))}
              </div>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={!title.trim()}
            onClick={() =>
              onSubmit({
                title,
                description,
                priority: priority === "none" ? null : priority,
                labels,
                work_type: workType || null,
                agent: agent === AUTO ? null : agent,
                model: model.trim() || null,
                transport: transport === "auto" ? null : transport,
                resume_session_id,
                resume_project_folder,
                scheduled_at: scheduledAt ? new Date(scheduledAt).toISOString() : null,
                analyst_agent_id: analystAgentId === AUTO ? null : analystAgentId,
                executor_agent_id: executorAgentId === AUTO ? null : executorAgentId,
              })
            }
          >
            {initial ? "Update" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
