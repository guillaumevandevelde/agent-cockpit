import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { MODAL_SIZES } from "@/lib/constants";
import { kanbanApi } from "../api";
import { PROVIDERS, PROVIDER_LABELS, DEFAULT_MODEL_SUGGESTIONS, MINIMAX_MODEL_SUGGESTIONS, modelSuggestionsForProvider } from "../types";
import type { KanbanColumn } from "../types";
import {
  hasClosedModelSet,
  modelForProviderChange,
  modelForProviderLoad,
} from "../columnModel";

const BACKLOG_COLUMN = "Backlog";
const DEFAULT_PROVIDER_SENTINEL = "__default__";

export function ColumnSettingsDialog({
  open,
  projectKey,
  projectPath,
  columns,
  onClose,
  onChanged,
}: {
  open: boolean;
  projectKey: string;
  projectPath: string;
  columns: KanbanColumn[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const [items, setItems] = useState<KanbanColumn[]>(columns);
  const [availableAgents, setAvailableAgents] = useState<string[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<string>("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editAgent, setEditAgent] = useState<string>("");
  const [editProvider, setEditProvider] = useState<string>(DEFAULT_PROVIDER_SENTINEL);
  const [editModel, setEditModel] = useState<string>("");
  const [editMaxSessions, setEditMaxSessions] = useState<number | null>(null);
  // Per-lane RTK opt-in (kaart c31333bf…). Lives next to the project-wide
  // kill-switch in /api/v1/kanban/token-saver — both must be on for the
  // hook to land. Default false (empty state) because the button row
  // shows "Edit" → "Save"; the user opts in explicitly.
  const [editTokenSaver, setEditTokenSaver] = useState<boolean>(false);
  const [modelOptions, setModelOptions] = useState<string[]>([...DEFAULT_MODEL_SUGGESTIONS]);
  const [minimaxOptions, setMinimaxOptions] = useState<string[]>([
    ...MINIMAX_MODEL_SUGGESTIONS,
  ]);
  // One effective-model fetch per column; only columns whose effective
  // value diverges from their own defaults carry a non-null entry here.
  const [effectiveByColumn, setEffectiveByColumn] = useState<
    Record<string, {
      provider: string;
      model: string | null;
      provider_source: string;
      model_source: string;
      global_override: { provider: string; model: string | null } | null;
      pool_choice: { provider: string; model: string | null } | null;
    }>
  >({});
  const effectiveModelFor = (id: string) => effectiveByColumn[id];

  useEffect(() => {
    setItems(columns);
  }, [columns]);

  useEffect(() => {
    if (!projectPath) return;
    kanbanApi.agents(projectPath).then((r) => setAvailableAgents(r.agents));
  }, [projectPath]);

  useEffect(() => {
    if (!open) return;
    kanbanApi.getModelOptions()
      .then((r) => { if (Array.isArray(r?.options)) setModelOptions(r.options); })
      .catch(() => {});
    kanbanApi.getMinimaxModelOptions()
      .then((r) => { if (Array.isArray(r?.options)) setMinimaxOptions(r.options); })
      .catch(() => {});
    // Fetch effective-model info for every column in parallel — only
    // entries that diverge from the column's own defaults land in state,
    // so a quiet board (no override / pool / persona override) stays
    // visually quiet. The resolve endpoint is cheap (one DB read, no
    // filesystem / CLI probes), so fanning out across N columns is fine.
    const next: typeof effectiveByColumn = {};
    Promise.all(
      items.map(async (col) => {
        try {
          const r = await kanbanApi.getColumnEffectiveModel(col.id);
          // Only keep rows where an override / pool / persona is actually
          // winning — the column's own defaults are already visible above.
          if (
            (r.provider_source !== "column_default" && r.provider_source !== "none") ||
            (r.model_source !== "column_default" && r.model_source !== "none")
          ) {
            next[col.id] = r;
          }
        } catch {
          // 404 / network hiccup — leave the column's own defaults as
          // the only signal. The Save path is unaffected.
        }
      }),
    ).then(() => setEffectiveByColumn(next));
  }, [open, items]);

  const handleRefreshModels = async () => {
    try {
      const r = await kanbanApi.refreshModelOptions();
      if (Array.isArray(r?.options)) setModelOptions(r.options);
    } catch {
      toast.error("Failed to refresh model list");
    }
    try {
      const r = await kanbanApi.refreshMinimaxModelOptions();
      if (Array.isArray(r?.options)) setMinimaxOptions(r.options);
    } catch {
      // The minimax refresh scans JSONL logs; a transient IO error here is
      // not worth toasting on top of the claude-side error above. The
      // cached list (seed or last-good) is left in place.
    }
  };

  const handleCreate = async () => {
    if (!selectedAgent) {
      toast.error("Select an agent first");
      return;
    }
    const name = selectedAgent;
    if (items.some((c) => c.name === name)) {
      toast.error("Column for this agent already exists");
      return;
    }
    try {
      const col = await kanbanApi.createColumn({
        project_key: projectKey,
        name,
        default_agent: name,
      });
      setItems((prev) => [...prev, col]);
      setSelectedAgent("");
      onChanged();
    } catch {
      toast.error("Failed to create column");
    }
  };

  const handleUpdate = async (id: string) => {
    const agent = editAgent.trim() || null;
    const provider = editProvider === DEFAULT_PROVIDER_SENTINEL ? null : editProvider;
    const model = editModel.trim() || null;
    try {
      const col = await kanbanApi.updateColumn(id, {
        default_agent: agent,
        default_provider: provider,
        default_model: model,
        max_sessions: editMaxSessions,
        token_saver_enabled: editTokenSaver,
      });
      setItems((prev) => prev.map((c) => (c.id === id ? col : c)));
      setEditingId(null);
      onChanged();
    } catch (e) {
      // Surface the backend's reason. The co-validation guard returns a 422
      // that names the offending pair ("model 'opus' is not valid for
      // provider 'minimax'; known options: [...]") and apiClient rethrows it
      // as error.message — swallowing that behind a generic string is what
      // left the stale-column failure undiagnosable.
      toast.error(e instanceof Error ? e.message : "Failed to update column");
    }
  };

  const handleDelete = async (id: string, name: string) => {
    if (name === BACKLOG_COLUMN) {
      toast.error("Cannot delete the Backlog column");
      return;
    }
    try {
      await kanbanApi.deleteColumn(id);
      setItems((prev) => prev.filter((c) => c.id !== id));
      onChanged();
    } catch {
      toast.error("Failed to delete column");
    }
  };

  const isBacklog = (name: string) => name === BACKLOG_COLUMN;
  const usedAgents = items.map((c) => c.default_agent).filter(Boolean);

  // Only one column is in edit mode at a time (`editingId`), so the edit
  // row's provider/model derivation is component-level, not per-column.
  const editProviderResolved =
    editProvider === DEFAULT_PROVIDER_SENTINEL ? null : editProvider;
  const editModelOptions =
    editProviderResolved === "minimax"
      ? minimaxOptions
      : modelSuggestionsForProvider(editProviderResolved, modelOptions);

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className={MODAL_SIZES.MD}>
        <DialogHeader>
          <DialogTitle>Column Settings</DialogTitle>
          <DialogDescription>
            The Backlog column is always present. Add columns by selecting an
            agent from the dropdown — the column will take the agent's name.
            Each column's session limit can be a number (max concurrent
            sessions), ∞ (no per-column limit), or Paused — pausing stops new
            dispatches into that column while letting running sessions finish.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 max-h-80 overflow-y-auto">
          {items.map((col) => (
            <div
              key={col.id}
              className="flex items-center gap-2 p-2 rounded border"
            >
              {editingId === col.id ? (
                <>
                  <div className="flex-1">
                    <div className="text-sm font-medium">{col.name}</div>
                  </div>
                  <Select
                    value={editAgent}
                    onValueChange={setEditAgent}
                  >
                    <SelectTrigger className="w-48">
                      <SelectValue placeholder="Default agent" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__none__">None</SelectItem>
                      {availableAgents.map((a) => (
                        <SelectItem key={a} value={a}>
                          {a}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Select
                    value={editProvider}
                    onValueChange={(v) => {
                      // Kaart 1782fa43… follow-up: switching provider must
                      // clear the model when the current model doesn't fit
                      // the new provider — otherwise the user can save
                      // (provider=minimax, model=opus) and the column is
                      // stuck on opus. The helper does the clear/keep
                      // decision; see columnModel.ts.
                      const resolved = v === DEFAULT_PROVIDER_SENTINEL ? null : v;
                      const newSuggestions = resolved === "minimax"
                        ? minimaxOptions
                        : modelSuggestionsForProvider(resolved, modelOptions);
                      setEditProvider(v);
                      setEditModel(modelForProviderChange(
                        editModel, editProvider === DEFAULT_PROVIDER_SENTINEL ? null : editProvider,
                        resolved, newSuggestions,
                      ));
                    }}
                  >
                    <SelectTrigger className="w-40">
                      <SelectValue placeholder="Provider" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={DEFAULT_PROVIDER_SENTINEL}>Default (Anthropic)</SelectItem>
                      {PROVIDERS.map((p) => (
                        <SelectItem key={p} value={p}>
                          {PROVIDER_LABELS[p]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <div className="flex flex-col gap-1">
                    <label htmlFor={`default-model-${col.id}`} className="sr-only">
                      Default model
                    </label>
                    {/*
                      Providers whose model set the backend enforces (422 on
                      an unknown model) get a real dropdown; the rest keep
                      free text.

                      Why not a datalist for both: an `<input list>` filters
                      its suggestions by what is already in the field, so a
                      column stored as (minimax, opus) showed ZERO MiniMax
                      options — the models were present in the DOM but
                      unreachable. That was the "minimax shows no models"
                      report. A <select> always renders every option, so no
                      field value can filter the list out of existence.

                      Native <select> rather than the Radix Select used for
                      agent/provider above: Radix only mounts its items while
                      open, which is both untestable in jsdom (see the note
                      around the datalist test) and needless here — this is a
                      plain one-of-N choice with no custom item rendering.
                    */}
                    {hasClosedModelSet(editProviderResolved) ? (
                      <select
                        id={`default-model-${col.id}`}
                        className="h-8 w-32 rounded border bg-background px-2 text-sm"
                        value={editModel}
                        onChange={(e) => setEditModel(e.target.value)}
                      >
                        {/* Empty = "no column default"; the dispatch chain
                            picks the model (for minimax that resolves to
                            MINIMAX_DEFAULT_MODEL). */}
                        <option value="">Default</option>
                        {editModelOptions.map((m) => (
                          <option key={m} value={m}>
                            {m}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <>
                        <input
                          id={`default-model-${col.id}`}
                          list={`model-suggestions-${col.id}`}
                          className="h-8 w-32 rounded border bg-background px-2 text-sm"
                          placeholder="Default model"
                          value={editModel}
                          onChange={(e) => setEditModel(e.target.value)}
                        />
                        <datalist id={`model-suggestions-${col.id}`}>
                          {editModelOptions.map((m) => (
                            <option key={m} value={m} />
                          ))}
                        </datalist>
                      </>
                    )}
                    <button
                      type="button"
                      className="text-[10px] text-muted-foreground hover:text-foreground text-left"
                      onClick={handleRefreshModels}
                    >
                      Refresh
                    </button>
                  </div>
                  <div className="flex items-center gap-1">
                    <button
                      className="h-7 w-7 rounded border text-sm hover:bg-accent disabled:opacity-30"
                      onClick={() => setEditMaxSessions(Math.max(0, (editMaxSessions ?? 1) - 1))}
                      disabled={editMaxSessions === null || editMaxSessions <= 0}
                      title="Decrease max sessions"
                    >−</button>
                    <span className="w-16 text-center text-xs tabular-nums">
                      {editMaxSessions === null
                        ? "∞"
                        : editMaxSessions === 0
                          ? "Paused"
                          : editMaxSessions}
                    </span>
                    <button
                      className="h-7 w-7 rounded border text-sm hover:bg-accent"
                      onClick={() => setEditMaxSessions((editMaxSessions ?? 0) + 1)}
                      title="Increase max sessions"
                    >+</button>
                    <button
                      className="ml-1 h-7 rounded border px-2 text-[10px] hover:bg-accent"
                      onClick={() => setEditMaxSessions(null)}
                      title="No per-column limit (use project cap)"
                    >∞</button>
                    <button
                      className="h-7 rounded border px-2 text-[10px] hover:bg-accent"
                      onClick={() => setEditMaxSessions(0)}
                      title="Pause — stops new dispatches into this column; running sessions keep going"
                    >Pause</button>
                  </div>
                  <label className="flex items-center gap-2 text-xs">
                    <input
                      type="checkbox"
                      checked={editTokenSaver}
                      onChange={(e) => setEditTokenSaver(e.target.checked)}
                      data-testid={`token-saver-toggle-${col.id}`}
                    />
                    Token saver (RTK)
                    <span className="text-muted-foreground">
                      — reduces Bash output
                    </span>
                  </label>
                  <Button size="sm" onClick={() => handleUpdate(col.id)}>
                    Save
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setEditingId(null)}>
                    Cancel
                  </Button>
                </>
              ) : (
                <>
                  <div className="flex-1">
                    <div className="text-sm font-medium">{col.name}</div>
                    {col.default_agent && (
                      <div className="text-xs text-muted-foreground">
                        Agent: {col.default_agent}
                      </div>
                    )}
                    {col.default_provider && (
                      <div className="text-xs text-muted-foreground">
                        Provider: {PROVIDER_LABELS[col.default_provider] ?? col.default_provider}
                      </div>
                    )}
                    {col.default_model && (
                      <div className="text-xs text-muted-foreground">
                        Model: {col.default_model}
                      </div>
                    )}
                    {col.token_saver_enabled && (
                      <div
                        className="text-xs text-emerald-600 dark:text-emerald-400"
                        data-testid={`token-saver-badge-${col.id}`}
                        title="RTK token-saver hook is installed for dispatches into this column (subject to the project-wide kill-switch)"
                      >
                        Token saver: on
                      </div>
                    )}
                    {/* Effective-model precedence line. Only surfaces when an
                        override / pool / persona is silently winning over the
                        column's own defaults (kaart 1782fa43…). A user editing
                        the column sees *why* their change isn't applied. */}
                    {effectiveModelFor(col.id) && (
                      <EffectiveModelLine
                        effective={effectiveModelFor(col.id)!}
                      />
                    )}
                  </div>
                  <div
                    className="text-xs tabular-nums mr-2"
                    title="Max concurrent sessions — Paused (0) stops new dispatches; running sessions are not interrupted"
                  >
                    {col.max_sessions === 0 ? (
                      <span className="font-medium text-amber-600 dark:text-amber-500">Paused</span>
                    ) : (
                      <span className="text-muted-foreground">
                        {col.max_sessions != null && col.max_sessions > 0 ? `max ${col.max_sessions}` : "∞"}
                      </span>
                    )}
                  </div>
                  {!isBacklog(col.name) && (
                    <>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setEditingId(col.id);
                          setEditAgent(col.default_agent ?? "");
                          setEditProvider(col.default_provider ?? DEFAULT_PROVIDER_SENTINEL);
                          // Rows persisted before the (provider, model)
                          // co-validation landed can hold an invalid pair —
                          // the live board had (minimax, opus). Loading that
                          // verbatim put the form in a state the backend
                          // refuses to save, so the column could not be
                          // repaired through the UI. Drop the unusable value
                          // instead of presenting it as the current setting.
                          setEditModel(modelForProviderLoad(
                            col.default_model,
                            col.default_provider,
                            col.default_provider === "minimax"
                              ? minimaxOptions
                              : modelSuggestionsForProvider(col.default_provider, modelOptions),
                          ));
                          setEditMaxSessions(col.max_sessions);
                          setEditTokenSaver(col.token_saver_enabled ?? false);
                        }}
                      >
                        Edit
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="text-destructive"
                        onClick={() => handleDelete(col.id, col.name)}
                      >
                        Delete
                      </Button>
                    </>
                  )}
                </>
              )}
            </div>
          ))}
        </div>

        <div className="flex gap-2 pt-2 border-t">
          <Select value={selectedAgent} onValueChange={setSelectedAgent}>
            <SelectTrigger className="flex-1">
              <SelectValue placeholder="Select agent to add column" />
            </SelectTrigger>
            <SelectContent>
              {availableAgents
                .filter((a) => !usedAgents.includes(a))
                .map((a) => (
                  <SelectItem key={a} value={a}>
                    {a}
                  </SelectItem>
                ))}
            </SelectContent>
          </Select>
          <Button onClick={handleCreate} disabled={!selectedAgent}>
            Add Column
          </Button>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// Maps a precedence-source label to a short human-readable reason.
// Order matches the dispatch precedence in `dispatch.py` (board-wide
// override > pool > column_override > column.default > persona).
const SOURCE_LABEL: Record<string, string> = {
  global_override: "board-wide subscription pin",
  pool: "subscription pool",
  column_override: "per-card column override",
  card_model: "card-level model",
  column_default: "column default",
  persona: "persona frontmatter",
  none: "no model set",
};

/** One-line "Will use X (source: …)" beneath the model defaults.

  Renders only when an override / pool / persona is silently winning over
  the column's own defaults (kaart 1782fa43…). Shows the effective
  provider/model + which precedence level won, so the user editing the
  column understands why their saved setting isn't applied at dispatch
  time.
*/
function EffectiveModelLine({ effective }: {
  effective: {
    provider: string;
    model: string | null;
    provider_source: string;
    model_source: string;
    global_override: { provider: string; model: string | null } | null;
    pool_choice: { provider: string; model: string | null } | null;
  };
}) {
  const providerLabel = PROVIDER_LABELS[effective.provider] ?? effective.provider;
  const modelLabel = effective.model ?? "(provider default)";
  // Source priority: pick the most interesting source — provider_source
  // outranks model_source only when they disagree, otherwise take the
  // more descriptive of the two.
  const interestingSource =
    effective.provider_source !== "column_default" && effective.provider_source !== "none"
      ? effective.provider_source
      : effective.model_source;
  const sourceLabel = SOURCE_LABEL[interestingSource] ?? interestingSource;
  // Detail: if the provider_source and model_source disagree (e.g.
  // pool pins provider only, column default supplies the model), show
  // both so the user can see the full chain.
  const showDetail =
    interestingSource === effective.provider_source &&
    effective.provider_source !== effective.model_source;
  return (
    <div
      className="text-[11px] text-amber-700 dark:text-amber-400 mt-1"
      title={`Effective provider: ${providerLabel} (${effective.provider_source}); effective model: ${modelLabel} (${effective.model_source})`}
    >
      Effective: <span className="font-medium">{providerLabel}</span>
      {effective.model ? <> · <span className="font-medium">{effective.model}</span></> : null}
      <span className="text-muted-foreground"> — from {sourceLabel}</span>
      {showDetail && (
        <span className="text-muted-foreground">
          {" "}(model: {SOURCE_LABEL[effective.model_source] ?? effective.model_source})
        </span>
      )}
    </div>
  );
}
