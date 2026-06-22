import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { RefreshCw } from "lucide-react";
import { useProjectContext } from "@/contexts/ProjectContext";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid } from "recharts";
import { kanbanApi } from "../kanban/api";
import type { AgentStatsResponse } from "../kanban/types";

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = seconds / 60;
  if (mins < 60) return `${mins.toFixed(1)}m`;
  return `${(mins / 60).toFixed(1)}h`;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return `${n}`;
}

function formatRate(rate: number | null): string {
  return rate === null ? "—" : `${Math.round(rate * 100)}%`;
}

const outcomeConfig = {
  completed: { label: "Completed", color: "hsl(var(--chart-2))" },
  failed: { label: "Failed", color: "hsl(var(--chart-5))" },
} satisfies ChartConfig;

const tokenConfig = {
  total_tokens: { label: "Tokens", color: "hsl(var(--chart-1))" },
} satisfies ChartConfig;

export function AgentPerformancePage() {
  const { activeProject } = useProjectContext();
  const projectPath = activeProject?.path ?? "";
  const [projectKey, setProjectKey] = useState("");
  const [stats, setStats] = useState<AgentStatsResponse | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!projectPath) return;
    kanbanApi.projectKey(projectPath).then((r) => setProjectKey(r.project_key));
  }, [projectPath]);

  const reload = useCallback(async () => {
    if (!projectKey) return;
    setLoading(true);
    try {
      setStats(await kanbanApi.stats(projectKey));
    } catch {
      toast.error("Failed to load agent stats");
    } finally {
      setLoading(false);
    }
  }, [projectKey]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const taskChartData = useMemo(
    () =>
      (stats?.agents ?? []).map((a) => ({
        agent: a.agent,
        completed: a.completed,
        failed: a.failed,
      })),
    [stats]
  );

  const tokenChartData = useMemo(
    () =>
      (stats?.agents ?? [])
        .filter((a) => a.total_tokens > 0)
        .map((a) => ({ agent: a.agent, total_tokens: a.total_tokens })),
    [stats]
  );

  if (!projectPath) return <div className="p-6">Select a project first.</div>;

  const totals = stats?.totals;

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Agent Performance</h1>
          <p className="text-sm text-muted-foreground">
            Time per task, success rate, token use and failures — derived from the
            kanban op-log.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={reload} disabled={loading}>
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="Tasks completed" value={`${totals?.completed ?? 0}`}
          sub={`${totals?.total_tasks ?? 0} total · ${totals?.in_progress ?? 0} in progress`} />
        <StatCard label="Success rate" value={formatRate(totals?.success_rate ?? null)}
          sub={`${totals?.failed ?? 0} failed`} />
        <StatCard label="Avg time / task"
          value={formatDuration(totals?.avg_duration_seconds ?? null)} sub="across all agents" />
        <StatCard label="Agents active" value={`${stats?.agents.length ?? 0}`}
          sub={stats?.tokens_available ? "token data linked" : "no token data"} />
      </div>

      {taskChartData.length === 0 ? (
        <Card>
          <CardContent className="h-[200px] flex items-center justify-center text-muted-foreground">
            No completed agent tasks yet for this project.
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Tasks per agent</CardTitle>
              <CardDescription>Completed vs. failed</CardDescription>
            </CardHeader>
            <CardContent>
              <ChartContainer config={outcomeConfig} className="h-[260px] w-full">
                <BarChart data={taskChartData} accessibilityLayer>
                  <CartesianGrid vertical={false} />
                  <XAxis dataKey="agent" tickLine={false} axisLine={false} tickMargin={8} />
                  <YAxis tickLine={false} axisLine={false} allowDecimals={false} />
                  <ChartTooltip content={<ChartTooltipContent />} />
                  <Bar dataKey="completed" stackId="a" fill="var(--color-completed)" radius={[0, 0, 4, 4]} />
                  <Bar dataKey="failed" stackId="a" fill="var(--color-failed)" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ChartContainer>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Token usage per agent</CardTitle>
              <CardDescription>
                {stats?.tokens_available
                  ? "Total tokens across dispatched sessions"
                  : "No token usage could be linked to agents"}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {tokenChartData.length === 0 ? (
                <div className="h-[260px] flex items-center justify-center text-muted-foreground text-sm">
                  No token data available
                </div>
              ) : (
                <ChartContainer config={tokenConfig} className="h-[260px] w-full">
                  <BarChart data={tokenChartData} accessibilityLayer>
                    <CartesianGrid vertical={false} />
                    <XAxis dataKey="agent" tickLine={false} axisLine={false} tickMargin={8} />
                    <YAxis tickLine={false} axisLine={false} tickFormatter={(v: number) => formatTokens(v)} />
                    <ChartTooltip
                      content={<ChartTooltipContent formatter={(v: unknown) => formatTokens(v as number)} />}
                    />
                    <Bar dataKey="total_tokens" fill="var(--color-total_tokens)" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ChartContainer>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {(stats?.agents.length ?? 0) > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Per-agent breakdown</CardTitle>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="py-2 pr-4 font-medium">Agent</th>
                  <th className="py-2 pr-4 font-medium text-right">Tasks</th>
                  <th className="py-2 pr-4 font-medium text-right">Success</th>
                  <th className="py-2 pr-4 font-medium text-right">Avg time</th>
                  <th className="py-2 pr-4 font-medium text-right">Median</th>
                  <th className="py-2 pr-4 font-medium text-right">In progress</th>
                  <th className="py-2 pr-4 font-medium text-right">Tokens</th>
                </tr>
              </thead>
              <tbody>
                {stats!.agents.map((a) => (
                  <tr key={a.agent} className="border-b last:border-0">
                    <td className="py-2 pr-4 font-medium">{a.agent}</td>
                    <td className="py-2 pr-4 text-right">
                      {a.completed}/{a.completed + a.failed}
                    </td>
                    <td className="py-2 pr-4 text-right">{formatRate(a.success_rate)}</td>
                    <td className="py-2 pr-4 text-right">{formatDuration(a.avg_duration_seconds)}</td>
                    <td className="py-2 pr-4 text-right">{formatDuration(a.median_duration_seconds)}</td>
                    <td className="py-2 pr-4 text-right">{a.in_progress}</td>
                    <td className="py-2 pr-4 text-right">
                      {a.total_tokens > 0 ? formatTokens(a.total_tokens) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Most common failures</CardTitle>
          <CardDescription>Impediments raised by agents, most frequent first</CardDescription>
        </CardHeader>
        <CardContent>
          {(stats?.common_failures.length ?? 0) === 0 ? (
            <div className="text-sm text-muted-foreground">No failures recorded.</div>
          ) : (
            <ul className="space-y-2">
              {stats!.common_failures.map((f, i) => (
                <li key={i} className="flex items-start gap-3 text-sm">
                  <span className="mt-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-muted px-1.5 text-xs font-medium">
                    {f.count}
                  </span>
                  <span className="flex-1">
                    {f.reason}
                    {f.agent && (
                      <span className="ml-2 text-xs text-muted-foreground">({f.agent})</span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
        <div className="mt-1 text-2xl font-semibold">{value}</div>
        {sub && <div className="mt-1 text-xs text-muted-foreground">{sub}</div>}
      </CardContent>
    </Card>
  );
}
