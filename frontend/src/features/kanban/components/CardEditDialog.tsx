import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ImagePlus, Trash2 } from "lucide-react";
import { toast } from "sonner";
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
import { fetchEndpoints, fetchResumableSessions } from "@/features/cc-bridge/api";
import { useProviderContext } from "@/contexts/ProviderContext";
import { PRIORITIES, PROVIDERS, PROVIDER_LABELS, WORK_TYPES, DEFAULT_MODEL_SUGGESTIONS, modelSuggestionsForProvider, MINIMAX_MODEL_SUGGESTIONS, type Priority, type WorkType, type ColumnOverride, type SubagentCaps, type KanbanColumn } from "../types";
import { kanbanApi } from "../api";
import type { EndpointResponse } from "@/features/cc-bridge/types";
import type { ResumableSession } from "@/types/sessions";

function parseLabels(raw: string): string[] {
  return raw
    .split(",")
    .map((l) => l.trim())
    .filter(Boolean);
}

const AUTO = "__auto__"; // sentinel: null agent (dispatch resolves the provider at run time)
const NO_WORK_TYPE = ""; // sentinel: no work_type set (routing hint is purely optional)
const DEFAULT_PROVIDER_SENTINEL = "__default__"; // per-column provider: no override (use column default)
const COMPATIBLE_PROVIDER = "anthropic-compatible";

// Form-side shape for one per-column override row. Provider uses the sentinel
// above to mean "no override"; the model is free text. Serialized to the
// ColumnOverride API shape ({model, provider} with nulls) on submit.
//
// subagent_caps_draft is the *form* shape (empty strings for unset fields
// so the inputs stay controlled). Serialised to the SubagentCaps API shape
// (undefined for unset) on submit; the backend validator handles the
// rest (kaart aaa81b23…).
type SubagentCapsDraft = {
  max_spawn_depth: string;
  max_concurrent: string;
  max_subagents_per_session: string;
  max_web_searches_per_session: string;
};

type OverrideDraft = {
  model: string;
  provider: string;
  endpoint_name: string;
  subagent_caps_draft: SubagentCapsDraft;
};

const EMPTY_SUBAGENT_CAPS_DRAFT: SubagentCapsDraft = {
  max_spawn_depth: "",
  max_concurrent: "",
  max_subagents_per_session: "",
  max_web_searches_per_session: "",
};

function draftsFromOverrides(
  overrides: Record<string, ColumnOverride> | null | undefined,
): Record<string, OverrideDraft> {
  const out: Record<string, OverrideDraft> = {};
  for (const [name, ov] of Object.entries(overrides ?? {})) {
    const caps = ov?.subagent_caps ?? null;
    out[name] = {
      model: ov?.model ?? "",
      provider: ov?.provider ?? DEFAULT_PROVIDER_SENTINEL,
      endpoint_name: ov?.endpoint_name ?? "",
      subagent_caps_draft: {
        max_spawn_depth:
          caps?.max_spawn_depth !== undefined && caps?.max_spawn_depth !== null
            ? String(caps.max_spawn_depth)
            : "",
        max_concurrent:
          caps?.max_concurrent !== undefined && caps?.max_concurrent !== null
            ? String(caps.max_concurrent)
            : "",
        max_subagents_per_session:
          caps?.max_subagents_per_session !== undefined &&
          caps?.max_subagents_per_session !== null
            ? String(caps.max_subagents_per_session)
            : "",
        max_web_searches_per_session:
          caps?.max_web_searches_per_session !== undefined &&
          caps?.max_web_searches_per_session !== null
            ? String(caps.max_web_searches_per_session)
            : "",
      },
    };
  }
  return out;
}

function _subagent_caps_draft_to_value(
  draft: SubagentCapsDraft,
): SubagentCaps | null {
  // Empty / invalid fields -> omitted; non-empty ints keep their value.
  // We only emit the dict when at least one field has a non-empty integer;
  // otherwise the wire payload stays clean (no empty `subagent_caps: {}`
  // round-trip that would confuse the next save).
  const out: SubagentCaps = {};
  for (const [key, raw] of Object.entries(draft) as [
    keyof SubagentCapsDraft,
    string,
  ][]) {
    const trimmed = raw.trim();
    if (!trimmed) continue;
    const n = Number(trimmed);
    if (!Number.isFinite(n) || !Number.isInteger(n) || n < 0) continue;
    out[key] = n;
  }
  return Object.keys(out).length ? out : null;
}

function overridesFromDrafts(
  drafts: Record<string, OverrideDraft>,
): Record<string, ColumnOverride> | null {
  const out: Record<string, ColumnOverride> = {};
  for (const [name, d] of Object.entries(drafts)) {
    const model = d.model.trim() || null;
    const provider =
      d.provider === DEFAULT_PROVIDER_SENTINEL ? null : d.provider;
    const endpoint_name =
      provider === COMPATIBLE_PROVIDER ? (d.endpoint_name.trim() || null) : null;
    const subagent_caps = _subagent_caps_draft_to_value(d.subagent_caps_draft);
    if (model || provider || subagent_caps) {
      const entry: ColumnOverride = { model, provider, endpoint_name };
      if (subagent_caps) entry.subagent_caps = subagent_caps;
      out[name] = entry;
    }
  }
  return Object.keys(out).length ? out : null;
}

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
  projectKey,
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
    column_overrides?: Record<string, ColumnOverride> | null;
    transport?: string | null;
    resume_session_id?: string | null;
    resume_project_folder?: string | null;
    scheduled_at?: string | null;
    analyst_agent_id?: string | null;
    executor_agent_id?: string | null;
  };
  defaultAgent?: string | null;
  projectKey?: string;
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
    column_overrides: Record<string, ColumnOverride> | null;
    transport: string | null;
    resume_session_id: string | null;
    resume_project_folder: string | null;
    scheduled_at: string | null;
    analyst_agent_id: string | null;
    executor_agent_id: string | null;
    // Screenshots staged in the dialog (create mode only). The caller uploads
    // them after the card exists — see KanbanPage's create handler.
    attachments: File[];
  }) => void | Promise<void>;  // awaited — see `submitting`
}) {
  // One submit at a time: a create can stall for seconds under board write
  // contention, so every impatient click used to create another card (three
  // identical ones on 2026-08-17). Pinned by the in-flight test.
  const [submitting, setSubmitting] = useState(false);
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
  const [minimaxOptions, setMinimaxOptions] = useState<string[]>([
    ...MINIMAX_MODEL_SUGGESTIONS,
  ]);
  const [analystAgentId, setAnalystAgentId] = useState<string>(initial?.analyst_agent_id ?? AUTO);
  const [executorAgentId, setExecutorAgentId] = useState<string>(initial?.executor_agent_id ?? AUTO);
  const [showAdvanced, setShowAdvanced] = useState<boolean>(
    !!(initial?.analyst_agent_id || initial?.executor_agent_id)
  );
  // Subagent caps: auto-open when an override already carries caps so an
  // operator re-opening a card doesn't have to discover the disclosure.
  const [showSubagentCaps, setShowSubagentCaps] = useState<boolean>(
    () => Object.values(initial?.column_overrides ?? {}).some(
      (ov) => ov && ov.subagent_caps && Object.keys(ov.subagent_caps).length > 0,
    )
  );
  const [columns, setColumns] = useState<KanbanColumn[]>([]);
  const [endpoints, setEndpoints] = useState<EndpointResponse[]>([]);
  const [overrideDrafts, setOverrideDrafts] = useState<Record<string, OverrideDraft>>(
    () => draftsFromOverrides(initial?.column_overrides)
  );
  const [transport, setTransport] = useState<string>(initial?.transport ?? "auto");
  const [scheduledAt, setScheduledAt] = useState<string>(
    initial?.scheduled_at ? toDatetimeLocalValue(initial.scheduled_at) : ""
  );
  const { providers } = useProviderContext();
  const installedProviders = providers.filter((p) => p.installed);

  // Screenshots staged for a *new* card. A card must exist before
  // POST /cards/{id}/attachments can run, so the files are kept in memory here
  // and uploaded by the caller right after createCard resolves. Editing an
  // existing card keeps using the drawer's Attachments tab (which uploads
  // immediately), hence create-mode only.
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [staged, setStaged] = useState<{ file: File; previewUrl: string }[]>([]);
  // Mirror of `staged` for the unmount cleanup below: that effect runs once, so
  // reading the state variable there would revoke a stale (first-render) list.
  const stagedRef = useRef(staged);
  useEffect(() => {
    stagedRef.current = staged;
  }, [staged]);

  const addStagedFiles = (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const accepted: { file: File; previewUrl: string }[] = [];
    for (const file of Array.from(files)) {
      if (!file.type.startsWith("image/")) {
        toast.error(`${file.name} is geen afbeelding`);
        continue;
      }
      accepted.push({ file, previewUrl: URL.createObjectURL(file) });
    }
    setStaged((prev) => [...prev, ...accepted]);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const removeStagedFile = (previewUrl: string) => {
    setStaged((prev) => prev.filter((s) => s.previewUrl !== previewUrl));
    URL.revokeObjectURL(previewUrl);
  };

  // Release the object URLs when the dialog unmounts so the blobs aren't
  // retained for the lifetime of the page.
  useEffect(() => {
    return () => {
      for (const s of stagedRef.current) URL.revokeObjectURL(s.previewUrl);
    };
  }, []);

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
    kanbanApi.getMinimaxModelOptions()
      .then((r) => { if (Array.isArray(r?.options)) setMinimaxOptions(r.options); })
      .catch(() => {});
  }, [open]);

  // Load the project's columns so we can render one override row per agent
  // column. Guarded on projectKey so the dialog still works when opened
  // without a project (e.g. in unit tests) — the override section just
  // renders empty then.
  useEffect(() => {
    if (!open || !projectKey) return;
    let cancelled = false;
    kanbanApi.listColumns(projectKey)
      .then((r) => { if (!cancelled) setColumns(r.columns ?? []); })
      .catch(() => { if (!cancelled) setColumns([]); });
    return () => { cancelled = true; };
  }, [open, projectKey]);

  useEffect(() => {
    if (!open || !projectKey) return;
    let cancelled = false;
    fetchEndpoints(projectKey)
      .then((r) => {
        if (!cancelled) setEndpoints(r.endpoints ?? []);
      })
      .catch(() => {
        if (!cancelled) setEndpoints([]);
      });
    return () => {
      cancelled = true;
    };
  }, [open, projectKey]);

  // Agent columns are those wired to a default_agent (persona). The dispatcher
  // keys per-card overrides on the column name, which equals the persona name,
  // so these are exactly the columns a user can override per card.
  const agentColumns = columns.filter((c) => c.default_agent);

  const setOverride = (name: string, patch: Partial<OverrideDraft>) =>
    setOverrideDrafts((prev) => {
      const base = prev[name] ?? {
        model: "",
        provider: DEFAULT_PROVIDER_SENTINEL,
        endpoint_name: "",
        subagent_caps_draft: { ...EMPTY_SUBAGENT_CAPS_DRAFT },
      };
      return { ...prev, [name]: { ...base, ...patch } };
    });

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

          {!initial && (
            <div className="space-y-2">
              <Label>Bijlagen</Label>
              <input
                ref={fileInputRef}
                type="file"
                aria-label="Bijlage kiezen"
                accept="image/png,image/jpeg,image/gif,image/webp"
                multiple
                className="hidden"
                onChange={(e) => addStagedFiles(e.target.files)}
              />
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => fileInputRef.current?.click()}
              >
                <ImagePlus className="h-4 w-4" />
                Screenshot toevoegen
              </Button>
              {staged.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  Optioneel — screenshots worden na het aanmaken geüpload en aan
                  de sessie meegegeven zodra de kaart gedispatcht wordt.
                </p>
              ) : (
                <div className="grid grid-cols-3 gap-2">
                  {staged.map((s) => (
                    <div
                      key={s.previewUrl}
                      className="group relative overflow-hidden rounded border bg-muted/30"
                    >
                      <img
                        src={s.previewUrl}
                        alt={s.file.name}
                        className="h-20 w-full object-cover"
                      />
                      <Button
                        size="icon"
                        variant="destructive"
                        className="absolute right-1 top-1 h-6 w-6 opacity-0 transition-opacity group-hover:opacity-100"
                        onClick={(e) => {
                          e.stopPropagation();
                          removeStagedFile(s.previewUrl);
                        }}
                        aria-label={`${s.file.name} verwijderen`}
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

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
              Global fallback: overrides the column default and persona
              frontmatter for this card only. Per-column overrides below win
              over it for their column.
            </p>
          </div>

          {agentColumns.length > 0 && (
            <div className="space-y-2">
              <Label>Model per agent-kolom</Label>
              <p className="text-xs text-muted-foreground">
                Override the model + provider for a specific agent column. A row
                left empty means no override — the column default is used.
              </p>
              <div className="space-y-2">
                {agentColumns.map((col) => {
                  const draft = overrideDrafts[col.name];
                  const modelValue = draft?.model ?? "";
                  const providerValue = draft?.provider ?? DEFAULT_PROVIDER_SENTINEL;
                  // The effective provider drives which model list is suggested:
                  // an explicit override wins, else the column default. When it
                  // resolves to minimax the datalist swaps to minimax models.
                  const effectiveProvider =
                    providerValue === DEFAULT_PROVIDER_SENTINEL ? col.default_provider : providerValue;
                  const rowSuggestions =
                    effectiveProvider === "minimax"
                      ? minimaxOptions
                      : modelSuggestionsForProvider(effectiveProvider, modelOptions);
                  const rowListId = `card-model-suggestions-${col.id}`;
                  const defaultLabel = [
                    col.default_model || null,
                    col.default_provider
                      ? PROVIDER_LABELS[col.default_provider] ?? col.default_provider
                      : "Anthropic",
                  ]
                    .filter(Boolean)
                    .join(" · ");
                  return (
                    <div key={col.id} className="grid grid-cols-[7rem_1fr_9rem] items-center gap-2">
                      <span className="text-sm font-medium truncate" title={col.name}>
                        {col.name}
                      </span>
                      <input
                        aria-label={`Model for ${col.name}`}
                        list={rowListId}
                        className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
                        placeholder={col.default_model || "(column default)"}
                        value={modelValue}
                        onChange={(e) => setOverride(col.name, { model: e.target.value })}
                      />
                      <datalist id={rowListId}>
                        {rowSuggestions.map((m) => (
                          <option key={m} value={m} />
                        ))}
                      </datalist>
                      <Select
                        value={providerValue}
                        onValueChange={(v) => {
                          if (v === COMPATIBLE_PROVIDER) {
                            setOverride(col.name, {
                              provider: v,
                              endpoint_name: endpoints[0]?.name ?? "",
                            });
                          } else {
                            setOverride(col.name, {
                              provider: v,
                              endpoint_name: "",
                            });
                          }
                        }}
                      >
                        <SelectTrigger aria-label={`Provider for ${col.name}`}>
                          <SelectValue placeholder="Default" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value={DEFAULT_PROVIDER_SENTINEL}>Default</SelectItem>
                          {PROVIDERS.map((p) => (
                            <SelectItem key={p} value={p}>
                              {PROVIDER_LABELS[p] ?? p}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      {providerValue === COMPATIBLE_PROVIDER &&
                        (endpoints.length === 0 ? (
                          <div className="col-span-3 -mt-1 flex items-center justify-between gap-3 rounded-md border border-dashed px-3 py-2">
                            <p className="text-xs text-muted-foreground">
                              Geen endpoints geconfigureerd — voeg er één toe via
                              de Endpoints-pagina.
                            </p>
                            <Link
                              to="/endpoints"
                              className="inline-flex h-8 items-center justify-center rounded-md border border-input bg-background px-3 text-xs shadow-sm hover:bg-accent hover:text-accent-foreground transition-colors"
                            >
                              Naar Endpoints
                            </Link>
                          </div>
                        ) : (
                          <div className="col-span-3 -mt-1">
                            <Select
                              value={
                                overrideDrafts[col.name]?.endpoint_name ?? ""
                              }
                              onValueChange={(endpoint_name) =>
                                setOverride(col.name, { endpoint_name })
                              }
                            >
                              <SelectTrigger
                                className="h-8"
                                aria-label={`Endpoint for ${col.name}`}
                              >
                                <SelectValue placeholder="Select endpoint" />
                              </SelectTrigger>
                              <SelectContent>
                                {endpoints.map((endpoint) => (
                                  <SelectItem
                                    key={endpoint.name}
                                    value={endpoint.name}
                                  >
                                    {endpoint.name} — {endpoint.model}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>
                        ))}
                      {providerValue !== COMPATIBLE_PROVIDER &&
                        defaultLabel && (
                        <p className="col-span-3 -mt-1 text-[10px] text-muted-foreground">
                          Default: {defaultLabel}
                        </p>
                      )}
                    </div>
                  );
                })}
              </div>

              <div className="mt-3 rounded-md border p-2">
                <button
                  type="button"
                  className="flex w-full items-center justify-between text-xs font-medium"
                  onClick={() => setShowSubagentCaps((v) => !v)}
                >
                  <span>Subagent caps (advanced)</span>
                  <span className="text-xs text-muted-foreground">{showSubagentCaps ? "Verbergen" : "Tonen"}</span>
                </button>
                {showSubagentCaps && (
                  <div className="space-y-2 pt-2">
                    <p className="text-[11px] text-muted-foreground">
                      Per-column Claude Code subagent/WebSearch caps (kaart aaa81b23…).
                      Wordt omgezet naar CLAUDE_CODE_MAX_* env vars bij dispatch.
                      Leeg = CC platform-default.
                    </p>
                    <div className="space-y-2">
                      {agentColumns.map((col) => {
                        const draft = overrideDrafts[col.name];
                        const capsDraft: SubagentCapsDraft =
                          draft?.subagent_caps_draft ?? EMPTY_SUBAGENT_CAPS_DRAFT;
                        return (
                          <div
                            key={`caps-${col.id}`}
                            className="grid grid-cols-[7rem_1fr_1fr] items-center gap-2"
                          >
                            <span className="text-xs font-medium truncate" title={col.name}>
                              {col.name}
                            </span>
                            <Input
                              type="number"
                              min={1}
                              max={3}
                              aria-label={`max_spawn_depth for ${col.name}`}
                              placeholder="max_spawn_depth (1-3)"
                              className="h-8 text-xs"
                              value={capsDraft.max_spawn_depth}
                              onChange={(e) =>
                                setOverride(col.name, {
                                  subagent_caps_draft: {
                                    ...capsDraft,
                                    max_spawn_depth: e.target.value,
                                  },
                                })
                              }
                            />
                            <Input
                              type="number"
                              min={0}
                              aria-label={`max_concurrent for ${col.name}`}
                              placeholder="max_concurrent"
                              className="h-8 text-xs"
                              value={capsDraft.max_concurrent}
                              onChange={(e) =>
                                setOverride(col.name, {
                                  subagent_caps_draft: {
                                    ...capsDraft,
                                    max_concurrent: e.target.value,
                                  },
                                })
                              }
                            />
                            <span className="text-[10px] text-muted-foreground col-span-3 -mt-1 ml-[7rem]">
                              max_subagents_per_session
                            </span>
                            <Input
                              type="number"
                              min={0}
                              aria-label={`max_subagents_per_session for ${col.name}`}
                              placeholder="max_subagents_per_session"
                              className="h-8 text-xs col-span-2"
                              value={capsDraft.max_subagents_per_session}
                              onChange={(e) =>
                                setOverride(col.name, {
                                  subagent_caps_draft: {
                                    ...capsDraft,
                                    max_subagents_per_session: e.target.value,
                                  },
                                })
                              }
                            />
                            <span className="text-[10px] text-muted-foreground col-span-3 -mt-1 ml-[7rem]">
                              max_web_searches_per_session
                            </span>
                            <Input
                              type="number"
                              min={0}
                              aria-label={`max_web_searches_per_session for ${col.name}`}
                              placeholder="max_web_searches_per_session"
                              className="h-8 text-xs col-span-2"
                              value={capsDraft.max_web_searches_per_session}
                              onChange={(e) =>
                                setOverride(col.name, {
                                  subagent_caps_draft: {
                                    ...capsDraft,
                                    max_web_searches_per_session: e.target.value,
                                  },
                                })
                              }
                            />
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {initial && (
          <div className="space-y-2 rounded-md border p-3">
            <button
              type="button"
              className="flex w-full items-center justify-between text-sm font-medium"
              onClick={() => setShowAdvanced((v) => !v)}
            >
              <span>Multi-agent split (geavanceerd)</span>
              <span className="text-xs text-muted-foreground">{showAdvanced ? "Verbergen" : "Tonen"}</span>
            </button>
            {showAdvanced && (
              <div className="space-y-3 pt-1">
                <p className="text-xs text-muted-foreground">
                  Kies een analyst-CLI om de two-phase split (analyst → executor)
                  te triggeren. Laat leeg voor een single-agent kaart.
                </p>
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
              </div>
            )}
          </div>
          )}

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
            disabled={!title.trim() || submitting}
            onClick={async () => {
              if (submitting) return;
              setSubmitting(true);
              try {
                await onSubmit({
                  title,
                  description,
                  priority: priority === "none" ? null : priority,
                  labels,
                  work_type: workType || null,
                  agent: agent === AUTO ? null : agent,
                  model: model.trim() || null,
                  column_overrides: overridesFromDrafts(overrideDrafts),
                  transport: transport === "auto" ? null : transport,
                  resume_session_id,
                  resume_project_folder,
                  scheduled_at: scheduledAt ? new Date(scheduledAt).toISOString() : null,
                  analyst_agent_id: analystAgentId === AUTO ? null : analystAgentId,
                  executor_agent_id: executorAgentId === AUTO ? null : executorAgentId,
                  attachments: staged.map((s) => s.file),
                });
              } finally {
                // Caller closes on success; on failure the dialog stays open,
                // so hand the button back for a retry.
                setSubmitting(false);
              }
            }}
          >
            {initial ? (submitting ? "Updating…" : "Update")
              : submitting ? "Creating…" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
