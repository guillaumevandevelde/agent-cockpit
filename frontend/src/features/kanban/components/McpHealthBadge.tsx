import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { kanbanApi } from "../api";
import type { McpHealth } from "../types";

/**
 * Live end-to-end status of the kanban MCP wiring. The failure it guards against
 * is silent: agents connect to the SSE stream but their tool calls 404 on a
 * mis-advertised message endpoint, so cards never get updated and nothing logs an
 * error. This badge turns that into a visible red signal. Click to re-check; hover
 * for the full diagnostic.
 */
export function McpHealthBadge() {
  const [health, setHealth] = useState<McpHealth | null>(null);
  const [loading, setLoading] = useState(false);

  const check = useCallback(async () => {
    setLoading(true);
    try {
      setHealth(await kanbanApi.mcpHealth());
    } catch (e) {
      setHealth({
        ok: false, advertised_endpoint: null, routes_to_mount: false,
        message_post_status: null, tools: [], db_ok: false,
        error: e instanceof Error ? e.message : "health check request failed",
      });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let alive = true;
    kanbanApi
      .mcpHealth()
      .then((h) => alive && setHealth(h))
      .catch(
        (e) =>
          alive &&
          setHealth({
            ok: false, advertised_endpoint: null, routes_to_mount: false,
            message_post_status: null, tools: [], db_ok: false,
            error: e instanceof Error ? e.message : "health check request failed",
          })
      );
    return () => {
      alive = false;
    };
  }, []);

  if (!health && !loading) return null;

  const ok = health?.ok ?? false;
  const label = loading && !health ? "MCP: checking…" : ok ? "MCP: healthy" : "MCP: broken";
  const title = health
    ? [
        `advertised endpoint: ${health.advertised_endpoint ?? "none"}`,
        `routes to mount: ${health.routes_to_mount}`,
        health.message_post_status != null ? `message POST status: ${health.message_post_status}` : null,
        `tools: ${health.tools.length ? health.tools.join(", ") : "none"}`,
        `store reachable: ${health.db_ok}`,
        health.error ? `error: ${health.error}` : null,
        "",
        "Click to re-check.",
      ].filter(Boolean).join("\n")
    : "Checking MCP wiring…";

  const checking = loading && !health;

  return (
    <Badge
      role="button"
      tabIndex={0}
      variant={ok ? "secondary" : "destructive"}
      title={title}
      className="cursor-pointer select-none gap-1.5"
      onClick={() => void check()}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          void check();
        }
      }}
    >
      <span
        aria-hidden
        className={cn(
          "h-2 w-2 shrink-0 rounded-full",
          checking ? "animate-pulse bg-muted-foreground" : ok ? "bg-emerald-500" : "bg-red-100",
        )}
      />
      {label}
    </Badge>
  );
}
