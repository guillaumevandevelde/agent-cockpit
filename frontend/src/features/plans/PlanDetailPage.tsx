import { useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, FileText, Calendar, HardDrive, KanbanSquare } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { MarkdownRenderer } from '@/components/shared/MarkdownRenderer'
import { RefreshButton } from '@/components/shared/RefreshButton'
import { usePlansApi } from '@/hooks/usePlansApi'
import { useFetchData } from '@/hooks/useFetchData'
import { formatBytes } from '@/types/backup'
import type {
  CorrelatedCardItem,
  DocContentResponse,
  DocSpecItem,
  PlansOverviewResponse,
} from '@/types/plans'

/**
 * Read-only detail view for a ``docs/cockpit/*.md`` file (Optie B section
 * C, kanban card 9e33a359).
 *
 * B-section rows (kanban card plans) bypass this page entirely: clicking
 * one in the list view navigates straight to ``/kanban?card=<card_id>``.
 * Only C-section clicks land here.
 *
 * The route param ``filename`` is the URL-encoded repo-relative doc
 * path (``docs/cockpit/foo.md`` → ``docs%2Fcockpit%2Ffoo.md``).
 * ``useParams`` gives us the decoded form.
 *
 * B↔C correlation (kanban plan 2026-07-28-plans-b-c-correlation
 * Task 2): alongside the doc content, we fetch the overview and look
 * up the current doc by path to render its ``implemented_by`` list as
 * a chip-list below the markdown body. Each chip is a kanban-card
 * link (``/kanban?card=<id>``) so the user can jump from a doc to the
 * cards that implement it — without the chip-list, the doc-detail page
 * would silently drop the correlation data the overview already
 * exposes on the list page.
 */
export function PlanDetailPage() {
  const { filename } = useParams<{ filename: string }>()
  const navigate = useNavigate()
  const { getDocContent, getOverview } = usePlansApi()

  // Doc content — single-doc fetch via the existing endpoint. Existing
  // loading/error UX is preserved: the markdown body only renders when
  // the doc fetch resolves, and a doc-side failure blocks the whole
  // detail view (the overview side is read-only context, not the
  // document the user actually came to read).
  const { data: doc, loading, error, refresh: fetchDoc } = useFetchData<DocContentResponse>(
    () => (filename ? getDocContent(filename) : Promise.resolve(null as unknown as DocContentResponse)),
    [filename, getDocContent]
  )

  // Overview — fetched in parallel with the doc body so the
  // chip-list can hydrate alongside the markdown. We deliberately
  // *don't* block the doc view on the overview fetch: the
  // overview is a non-critical adjacency, and the doc page must
  // keep showing the body even if the overview call fails
  // (network blip, project unmount). Failure is logged via the
  // shared ``useFetchData`` error path so the operator sees it,
  // but the doc body still renders.
  const {
    data: overview,
    loading: overviewLoading,
    error: overviewError,
  } = useFetchData<PlansOverviewResponse>(getOverview, [getOverview])

  // Find the current doc item in the overview by path. The match is
  // exact: the overview's ``docs[].path`` is repo-relative and the
  // URL-decoded ``filename`` is the same form.
  const currentDocItem = useMemo<DocSpecItem | null>(() => {
    if (!overview || !filename) return null
    return overview.docs.find((doc) => doc.path === filename) ?? null
  }, [overview, filename])

  const implementedBy = currentDocItem?.implemented_by ?? EMPTY_IMPLEMENTED_BY

  const backButton = (
    <Button variant="ghost" size="sm" onClick={() => navigate('/plans')} className="mb-2">
      <ArrowLeft className="h-4 w-4 mr-2" />
      Back to Plans
    </Button>
  )

  if (loading) {
    return (
      <div className="space-y-6">
        {backButton}
        <Card>
          <CardContent className="py-8">
            <p className="text-center text-muted-foreground">Loading doc…</p>
          </CardContent>
        </Card>
      </div>
    )
  }

  if (error || !doc) {
    return (
      <div className="space-y-6">
        {backButton}
        <Card className="border-destructive">
          <CardHeader>
            <CardTitle className="text-destructive">Error</CardTitle>
            <CardDescription>{error || 'Doc not found'}</CardDescription>
          </CardHeader>
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          {backButton}
          <h1 className="text-3xl font-bold flex items-center gap-2">
            <FileText className="h-8 w-8" />
            {doc.title}
          </h1>
          <div className="flex items-center gap-3 mt-2 text-sm text-muted-foreground">
            <span
              className="flex items-center gap-1"
              title={new Date(doc.modified_at).toLocaleString()}
            >
              <Calendar className="h-3 w-3" />
              {new Date(doc.modified_at).toLocaleDateString()}
            </span>
            <span className="flex items-center gap-1">
              <HardDrive className="h-3 w-3" />
              {formatBytes(doc.size_bytes)}
            </span>
          </div>
          <p className="text-xs text-muted-foreground mt-1 font-mono">{doc.path}</p>
        </div>
        <RefreshButton onClick={fetchDoc} loading={loading} />
      </div>

      <Card>
        <CardContent className="pt-6">
          <MarkdownRenderer content={doc.content} />
        </CardContent>
      </Card>

      <ImplementedBySection
        cards={implementedBy}
        loading={overviewLoading}
        error={overviewError}
        onCardClick={(cardId) => navigate(`/kanban?card=${cardId}`)}
      />
    </div>
  )
}

// Module-level empty array so ``useMemo`` deps don't churn on every
// render.
const EMPTY_IMPLEMENTED_BY: CorrelatedCardItem[] = []

// "Implemented by cards" chip-list, sourced from the overview fetch.
// Renders nothing while the overview is still loading or has no chips
// for the current doc — the doc body stays the primary content and
// the chip-list is a non-critical adjacency.
function ImplementedBySection({
  cards,
  loading,
  error,
  onCardClick,
}: {
  cards: CorrelatedCardItem[]
  loading: boolean
  error: string | null
  onCardClick: (cardId: string) => void
}) {
  if (loading && cards.length === 0) {
    return (
      <Card>
        <CardContent className="py-4 text-sm text-muted-foreground">
          Loading implemented-by cards…
        </CardContent>
      </Card>
    )
  }
  if (error && cards.length === 0) {
    return (
      <Card className="border-destructive">
        <CardContent className="py-4 text-sm text-destructive">
          Failed to load implemented-by cards: {error}
        </CardContent>
      </Card>
    )
  }
  if (cards.length === 0) return null
  return (
    <section aria-labelledby="implemented-by-heading">
      <Card>
        <CardHeader className="pb-3">
          <CardTitle
            id="implemented-by-heading"
            className="text-base flex items-center gap-2"
          >
            <KanbanSquare className="h-4 w-4" />
            Implemented by cards
          </CardTitle>
          <CardDescription>
            Kanban cards whose <code className="text-xs">metadata.spec_doc</code>{' '}
            points at this doc.
          </CardDescription>
        </CardHeader>
        <CardContent className="pt-0">
          <ul className="flex flex-wrap gap-1.5">
            {cards.map((card) => (
              <li key={card.card_id}>
                <a
                  href={`/kanban?card=${card.card_id}`}
                  onClick={(e) => {
                    e.preventDefault()
                    onCardClick(card.card_id)
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      onCardClick(card.card_id)
                    }
                  }}
                  className="inline-flex items-center gap-1 rounded-md border bg-secondary px-2 py-0.5 text-xs font-medium hover:bg-secondary/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring cursor-pointer"
                  aria-label={`Open kanban card ${card.card_title}`}
                  title={`Open kanban card: ${card.card_title}`}
                >
                  <KanbanSquare className="h-3 w-3" />
                  <span className="truncate max-w-[180px]">{card.card_title}</span>
                </a>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </section>
  )
}
