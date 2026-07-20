/**
 * WachtrijSection — "Wacht op jou" PO-facing view.
 *
 * Aggregates every human-blocked item across all tracked projects into one
 * finite, sortable list. Lives on the Projects page (the natural PO-facing
 * landing) so the product owner doesn't have to scan every kanban column +
 * the gate/review/plan_ref metadata edges manually. See kanban card
 * `c7ea21b0…` and `docs/cockpit/product-owner-volgbaarheid-analyse.md`
 * §2b/§4.1/§5 kaart B.
 *
 * Data flow:
 *   1. Read `projects` from `useProjectContext()` (each has a `path`).
 *   2. For each project, call `kanbanApi.projectKey(path)` to get the
 *      kanban-side `project_key` (the projects-list and kanban-DB share
 *      the path but identify projects by different ids).
 *   3. Fetch `/kanban/wachtrij?project_key=...` per project, in parallel.
 *   4. Merge all items, sort by `wait_seconds` desc (longest wait first —
 *      backend already sorts each project, but merging across projects
 *      needs its own pass).
 *   5. Render: empty state when nothing waits, otherwise a card per item
 *      with kind badge + reason + wait time + click-to-kanban.
 *
 * Click-to-card navigates to `/kanban?card=<id>` — the kanban page's
 * deep-link support (kanban card-references-analysis §2.4/§D2) handles
 * opening the right card drawer.
 */
import { useCallback, useEffect, useState, type ComponentType } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertCircle,
  Clock,
  Eye,
  Hourglass,
  ListChecks,
  MessageSquare,
  RefreshCw,
} from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useProjectContext } from '@/contexts/ProjectContext';
import { kanbanApi } from '@/features/kanban/api';
import {
  WACHTRIJ_KIND_LABELS,
  type WachtrijItem,
  type WachtrijKind,
} from '@/features/kanban/types';
import { cn } from '@/lib/utils';

interface MergedItem extends WachtrijItem {
  project_name: string;
  project_path: string;
}

// Per-kind icon mapping. Keeps the section compact and scannable when
// several items stack — the eye/clock/question glyph hints at "look at
// this" without forcing the user to read the badge label.
const KIND_ICONS: Record<WachtrijKind, ComponentType<{ className?: string }>> = {
  impediment_needs_answer: AlertCircle,
  gate_open: MessageSquare,
  review_requested: Eye,
  awaiting_plan_ref: Hourglass,
};

// Per-kind badge colour so the four categories stay visually distinct on
// a stack. Keeps the colour palette within the existing shadcn tokens —
// no new theme keys, no dark-mode drift.
const KIND_BADGE_CLASSES: Record<WachtrijKind, string> = {
  impediment_needs_answer: 'bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-200',
  gate_open: 'bg-blue-100 text-blue-900 dark:bg-blue-900/40 dark:text-blue-200',
  review_requested: 'bg-purple-100 text-purple-900 dark:bg-purple-900/40 dark:text-purple-200',
  awaiting_plan_ref: 'bg-slate-100 text-slate-900 dark:bg-slate-800 dark:text-slate-200',
};

function formatWait(seconds: number): string {
  if (seconds < 60) return 'just nu';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}u`;
  const days = Math.floor(seconds / 86400);
  return `${days}d`;
}

export function WachtrijSection() {
  const { projects } = useProjectContext();
  const [items, setItems] = useState<MergedItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Tracks which projects contributed at least one item so the empty
  // state can still name the scope ("over N projecten — niets wacht op je").
  const [scannedCount, setScannedCount] = useState(0);

  const load = useCallback(async () => {
    if (projects.length === 0) {
      setItems([]);
      setScannedCount(0);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      // Resolve kanban project keys + fetch wachtrij for each in parallel.
      // A failure on a single project (e.g. it isn't kanban-enabled yet)
      // must not block the rest — surface it as an empty contribution.
      const settled = await Promise.allSettled(
        projects.map(async (p): Promise<MergedItem[]> => {
          const keyRes = await kanbanApi.projectKey(p.path);
          const wachtrij = await kanbanApi.wachtrij(keyRes.project_key);
          return wachtrij.items.map((it) => ({
            ...it,
            project_name: p.name,
            project_path: p.path,
          }));
        }),
      );
      const merged: MergedItem[] = [];
      let failures = 0;
      for (const r of settled) {
        if (r.status === 'fulfilled') {
          merged.push(...r.value);
        } else {
          failures++;
        }
      }
      // Re-sort across projects: longest wait first.
      merged.sort((a, b) => b.wait_seconds - a.wait_seconds);
      setItems(merged);
      setScannedCount(projects.length);
      if (failures > 0 && merged.length === 0) {
        setError(
          `Kon ${failures} project${failures === 1 ? '' : 'en'} niet scannen op wachtrij-items. Vernieuw om het opnieuw te proberen.`,
        );
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Onbekende fout');
    } finally {
      setLoading(false);
    }
  }, [projects]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
        <div className="space-y-1">
          <CardTitle className="flex items-center gap-2 text-base">
            <ListChecks className="h-5 w-5 text-primary" />
            Wacht op jou
          </CardTitle>
          <CardDescription>
            Alles wat het bord nu blokkeert tot jij beslist — impedimenten,
            open gates, review-verzoeken en kaarten die op een plan wachten.
            {scannedCount > 0 && !loading && (
              <>
                {' '}
                <span className="text-muted-foreground/80">
                  {scannedCount} project{scannedCount === 1 ? '' : 'en'} gescand.
                </span>
              </>
            )}
          </CardDescription>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => void load()}
          disabled={loading}
          className="gap-2"
        >
          <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
          Vernieuw
        </Button>
      </CardHeader>

      <CardContent className="space-y-2">
        {error && (
          <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            {error}
          </div>
        )}

        {loading && items.length === 0 && (
          <div className="space-y-2" aria-busy="true">
            {[0, 1, 2].map((i) => (
              <div
                key={i}
                className="h-16 w-full animate-pulse rounded-md bg-muted"
              />
            ))}
          </div>
        )}

        {!loading && items.length === 0 && !error && (
          <div className="flex flex-col items-center justify-center gap-2 rounded-md border border-dashed py-8 text-center">
            <Clock className="h-8 w-8 text-muted-foreground/50" />
            <p className="font-medium">Niets wacht op jou</p>
            <p className="text-sm text-muted-foreground">
              {scannedCount === 0
                ? 'Geen projecten gevolgd — voeg er een toe om te starten.'
                : 'Het bord loopt zoals het hoort. Volgende wachtrij-item verschijnt hier zodra er iets blokkeert.'}
            </p>
          </div>
        )}

        {items.map((it) => {
          const Icon = KIND_ICONS[it.kind];
          return (
            <Link
              key={`${it.project_path}:${it.card_id}`}
              to={`/kanban?card=${it.card_id}`}
              className={cn(
                'block rounded-md border p-3 transition-colors',
                'hover:border-primary/50 hover:bg-accent/40',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
              )}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
                    <Badge
                      variant="secondary"
                      className={cn('text-xs', KIND_BADGE_CLASSES[it.kind])}
                    >
                      {WACHTRIJ_KIND_LABELS[it.kind]}
                    </Badge>
                    <span className="truncate text-sm font-medium">
                      {it.card_title}
                    </span>
                  </div>
                  <p className="line-clamp-2 text-sm text-muted-foreground">
                    {it.reason}
                  </p>
                  <p className="text-xs text-muted-foreground/70">
                    {it.project_name} · kolom {it.card_column}
                  </p>
                </div>
                <div className="shrink-0 text-right">
                  <p className="text-xs font-medium text-muted-foreground">
                    {formatWait(it.wait_seconds)}
                  </p>
                  <p className="text-xs text-muted-foreground/60">geleden</p>
                </div>
              </div>
            </Link>
          );
        })}
      </CardContent>
    </Card>
  );
}
