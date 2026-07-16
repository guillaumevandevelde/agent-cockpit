import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { kanbanApi } from "../api";
import { formatTokens, formatCost, shortenModelName, getModelColor } from "@/features/usage/utils";
import type { CardUsage } from "../cardUsage";
import type { Card } from "../types";

/**
 * Per-card token telemetry tab (kanban card 8a2ad986).
 *
 * Renders aggregated input/output/cache tokens + model breakdown for the
 * session that worked this card. Backed by GET /kanban/cards/{cid}/usage
 * which derives the data from Claude Code's per-session JSONL transcript —
 * the spawned session never sees a new tool/turn, so its own token bill is
 * unaffected (acceptance criterion #4).
 *
 * Re-fetches on tab visibility so a fresh dispatch (tokens appearing
 * seconds after spawn) eventually shows up without a manual refresh.
 */
export function CardTokensTab({ card }: { card: Card }) {
  const [usage, setUsage] = useState<CardUsage | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      kanbanApi
        .getCardUsage(card.id)
        .then((resp) => {
          if (!cancelled) {
            setUsage(resp.usage);
            setError(null);
          }
        })
        .catch((e: unknown) => {
          if (!cancelled) {
            setError(e instanceof Error ? e.message : String(e));
          }
        });
    };
    load();
    // Refresh while the card is in flight (dispatch is recent): the JSONL
    // appears within seconds of the first model call, so a 5s poll keeps
    // the UI honest without hammering the endpoint.
    const id = setInterval(load, 5_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [card.id]);

  // undefined = first fetch in flight; render the spinner explicitly so
  // we don't flash the "no telemetry" empty state during normal load.
  if (usage === undefined) {
    return (
      <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading token telemetry…
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 text-sm text-destructive">
        Failed to load token telemetry: {error}
      </div>
    );
  }

  // usage === null means the card has no dispatch breadcrumbs. Two cases:
  //   - Legacy card dispatched before this feature landed → permanent
  //     null. Surface the explanation so a curious operator doesn't
  //     report "the tokens tab is broken".
  //   - Card never dispatched yet (still on Backlog) → also null.
  if (usage === null) {
    return (
      <div className="p-4 text-sm text-muted-foreground">
        No token telemetry for this card. Cards dispatched before{" "}
        <span className="font-mono">2026-07-15</span> have no per-dispatch
        breadcrumbs recorded, and cards that haven't been dispatched yet
        naturally have none.
      </div>
    );
  }

  // usage is a CardUsage with zero tokens — dispatch was recorded but the
  // session's first JSONL hasn't landed yet. Most common when this tab is
  // opened during the first few seconds of a fresh dispatch.
  if (usage.session_id === null && usage.total_tokens === 0) {
    return (
      <div className="p-4 text-sm text-muted-foreground">
        <div className="flex items-center gap-2">
          <Loader2 className="h-4 w-4 animate-spin" />
          Awaiting first response…
        </div>
        <div className="mt-2 text-xs">
          Token totals will appear once the spawned session writes its first
          usage entry. Recorded model:{" "}
          <span className="font-mono">{usage.recorded_model ?? "(unset)"}</span>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4 p-4">
      <div className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
        <Stat label="Input" value={formatTokens(usage.input_tokens)} />
        <Stat label="Output" value={formatTokens(usage.output_tokens)} />
        <Stat
          label="Cache (creation + read)"
          value={formatTokens(usage.cache_creation_tokens + usage.cache_read_tokens)}
        />
        <Stat label="Cost (USD)" value={formatCost(usage.total_cost_usd)} />
      </div>

      <div className="rounded-md border bg-muted/30 p-3 text-xs text-muted-foreground">
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          <span>
            Session:{" "}
            <span className="font-mono">
              {usage.session_id ?? "(unresolved)"}
            </span>
          </span>
          <span>
            Recorded model:{" "}
            <span className="font-mono">
              {usage.recorded_model ?? "(unset)"}
            </span>
          </span>
          {usage.first_activity && usage.last_activity && (
            <span>
              Activity: {formatTimestamp(usage.first_activity)} →{" "}
              {formatTimestamp(usage.last_activity)}
            </span>
          )}
        </div>
      </div>

      {usage.model_breakdowns.length > 0 && (
        <div>
          <h4 className="mb-2 text-sm font-medium">Per-model breakdown</h4>
          <div className="overflow-hidden rounded-md border">
            <table className="w-full text-sm">
              <thead className="bg-muted/50">
                <tr>
                  <th className="px-3 py-2 text-left">Model</th>
                  <th className="px-3 py-2 text-right">Input</th>
                  <th className="px-3 py-2 text-right">Output</th>
                  <th className="px-3 py-2 text-right">Cache in</th>
                  <th className="px-3 py-2 text-right">Cache read</th>
                  <th className="px-3 py-2 text-right">Total</th>
                </tr>
              </thead>
              <tbody>
                {usage.model_breakdowns.map((b) => (
                  <tr key={b.model} className="border-t">
                    <td className="px-3 py-2">
                      <span
                        className="mr-2 inline-block h-2 w-2 rounded-full"
                        style={{ background: getModelColor(b.model) }}
                        aria-hidden="true"
                      />
                      {shortenModelName(b.model)}
                    </td>
                    <td className="px-3 py-2 text-right font-mono">
                      {formatTokens(b.input_tokens)}
                    </td>
                    <td className="px-3 py-2 text-right font-mono">
                      {formatTokens(b.output_tokens)}
                    </td>
                    <td className="px-3 py-2 text-right font-mono">
                      {formatTokens(b.cache_creation_tokens)}
                    </td>
                    <td className="px-3 py-2 text-right font-mono">
                      {formatTokens(b.cache_read_tokens)}
                    </td>
                    <td className="px-3 py-2 text-right font-mono">
                      {formatTokens(b.total_tokens)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="font-mono text-lg">{value}</div>
    </div>
  );
}

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}