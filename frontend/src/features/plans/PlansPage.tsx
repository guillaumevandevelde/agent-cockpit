import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ClipboardList, Search, FileText, KanbanSquare, BookText, ExternalLink, Link2 } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { RefreshButton } from '@/components/shared/RefreshButton'
import { usePlansApi } from '@/hooks/usePlansApi'
import { useFetchData } from '@/hooks/useFetchData'
import { CLICKABLE_CARD } from '@/lib/constants'
import { formatBytes } from '@/types/backup'
import type { CardPlanItem, CorrelatedCardItem, DocSpecItem, PlansOverviewResponse } from '@/types/plans'

// Module-level empty arrays so the ``useMemo`` deps below don't churn
// on every render. ``data?.cards ?? EMPTY_CARDS`` keeps a stable
// reference when the overview hasn't loaded yet.
const EMPTY_CARDS: CardPlanItem[] = []
const EMPTY_DOCS: DocSpecItem[] = []
const EMPTY_IMPLEMENTED_BY: CorrelatedCardItem[] = []

/**
 * Read-only "Plans & Specs" overview (Optie B, kanban card 9e33a359).
 *
 * Two sections, each carrying B↔C correlation data (kanban plan
 * 2026-07-28-plans-b-c-correlation Task 1):
 *   * **B — from cards.** ``plan``/``plan_ref`` deliverables attached to
 *     kanban cards scoped to the active project. Click a row to jump to
 *     the source card (``/kanban?card=<card_id>``). Rows whose card has
 *     a ``spec_doc`` anchor also surface a small inline doclink that
 *     navigates straight to the matching C row at
 *     ``/plans/<encoded-path>`` — without that link, the user would
 *     need to find the doc manually.
 *   * **C — from docs.** ``docs/cockpit/*.md`` files in the repo's SSOT
 *     tree. Click a row to open the doc detail page
 *     (``/plans/<encoded-path>``). Rows whose ``implemented_by`` list is
 *     non-empty render an "Implemented by cards" chip-list below the
 *     path; each chip is a kanban-card link (``/kanban?card=<id>``) so
 *     the user can jump from a doc to the cards that implement it.
 *
 * Inner links live inside CLICKABLE_CARD rows — they MUST call
 * ``stopPropagation()`` on their ``onClick`` so a chip click doesn't
 * also trigger the outer card's row-navigation. Convention from
 * ``HostsPage`` / ``MCPServersPage``.
 *
 * Each section has its own empty/loading/error state — independent
 * failures aren't possible today (one fetch, two sections) but the
 * shape is left ready for the day when they are.
 */
export function PlansPage() {
  const navigate = useNavigate()
  const { getOverview } = usePlansApi()
  const [searchQuery, setSearchQuery] = useState('')

  const { data, loading, error, refresh } = useFetchData<PlansOverviewResponse>(
    getOverview,
    [getOverview]
  )

  const cards = data?.cards ?? EMPTY_CARDS
  const docs = data?.docs ?? EMPTY_DOCS
  const projectKey = data?.project_key ?? ''

  const filteredCards = useMemo(
    () => filterCards(cards, searchQuery),
    [cards, searchQuery]
  )
  const filteredDocs = useMemo(
    () => filterDocs(docs, searchQuery),
    [docs, searchQuery]
  )

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-2">
            <ClipboardList className="h-8 w-8" />
            Plans &amp; Specs
          </h1>
          <p className="text-muted-foreground">
            Read-only window on the active project's plan attachments and
            the repo's cockpit docs.
          </p>
          {projectKey && (
            <p className="text-xs text-muted-foreground mt-1 font-mono">
              Project: {projectKey}
            </p>
          )}
        </div>
        <RefreshButton onClick={refresh} loading={loading} />
      </div>

      {error && (
        <Card className="border-destructive">
          <CardContent className="py-4 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <Input
          placeholder="Filter by title or path…"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="pl-10"
        />
      </div>

      <CardPlanSection
        items={filteredCards}
        loading={loading && cards.length === 0}
        searchQuery={searchQuery}
        onCardClick={(cardId) => navigate(`/kanban?card=${cardId}`)}
        onDocLinkClick={(path) => navigate(`/plans/${encodeURIComponent(path)}`)}
      />

      <DocSection
        items={filteredDocs}
        loading={loading && docs.length === 0}
        searchQuery={searchQuery}
        onDocClick={(path) => navigate(`/plans/${encodeURIComponent(path)}`)}
        onCardLinkClick={(cardId) => navigate(`/kanban?card=${cardId}`)}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Section B — kanban card plan attachments
// ---------------------------------------------------------------------------

function CardPlanSection({
  items,
  loading,
  searchQuery,
  onCardClick,
  onDocLinkClick,
}: {
  items: CardPlanItem[]
  loading: boolean
  searchQuery: string
  onCardClick: (cardId: string) => void
  onDocLinkClick: (path: string) => void
}) {
  return (
    <section aria-labelledby="card-plans-heading" className="space-y-3">
      <div className="flex items-center justify-between">
        <h2
          id="card-plans-heading"
          className="text-lg font-semibold flex items-center gap-2"
        >
          <KanbanSquare className="h-5 w-5" />
          From Kanban Cards
        </h2>
        <Badge variant="secondary" className="text-xs">
          {items.length}
        </Badge>
      </div>
      <p className="text-sm text-muted-foreground">
        Plan &amp; plan_ref deliverables attached to kanban cards in this project.
        Click a row to jump to the source card.
      </p>
      {loading ? (
        <SectionEmpty>Loading card plans…</SectionEmpty>
      ) : items.length === 0 ? (
        <SectionEmpty>
          {searchQuery ? 'No card plans match your filter' : 'No card plans in this project yet'}
        </SectionEmpty>
      ) : (
        <ul className="space-y-2">
          {items.map((item) => (
            <li key={item.deliverable_id}>
              <Card
                className={CLICKABLE_CARD}
                onClick={() => onCardClick(item.card_id)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    onCardClick(item.card_id)
                  }
                }}
                tabIndex={0}
                role="button"
                aria-label={`Open kanban card ${item.card_title}`}
              >
                <CardContent className="py-4">
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <KanbanSquare className="h-4 w-4 text-muted-foreground flex-shrink-0" />
                        <h3 className="font-medium truncate">{item.card_title}</h3>
                        <Badge variant="outline" className="text-xs">
                          {item.kind}
                        </Badge>
                      </div>
                      <p className="text-sm text-muted-foreground line-clamp-2">
                        {item.excerpt || '(no excerpt)'}
                      </p>
                      {item.spec_doc && (
                        <SpecDocLink
                          path={item.spec_doc}
                          onDocLinkClick={onDocLinkClick}
                        />
                      )}
                    </div>
                    <div className="flex flex-col items-end gap-1 flex-shrink-0">
                      <span
                        className="text-xs text-muted-foreground"
                        title={new Date(item.created_at).toLocaleString()}
                      >
                        {formatRelativeDate(item.created_at)}
                      </span>
                      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                        <ExternalLink className="h-3 w-3" />
                        <span className="font-mono">{item.card_id.slice(0, 8)}</span>
                      </span>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

// Inline B-side doclink (kanban plan 2026-07-28-plans-b-c-correlation
// Task 2). Lives inside a CLICKABLE_CARD — its onClick MUST
// ``stopPropagation()`` so a click on the link doesn't also navigate
// to the outer card's row destination (``/kanban?card=<id>``) AND
// ``preventDefault()`` so the browser doesn't follow the ``href`` and
// hard-reload the SPA (the raw-anchor + onClick + navigate pattern
// without preventDefault causes a full page reload on every chip
// click — see review finding I1). The link is rendered as an anchor
// element with an ``href`` so it's keyboard-focusable +
// middle-click-open + copyable, and so the SPA does the navigation
// via React Router rather than a full page reload.
function SpecDocLink({
  path,
  onDocLinkClick,
}: {
  path: string
  onDocLinkClick: (path: string) => void
}) {
  const href = `/plans/${encodeURIComponent(path)}`
  return (
    <div className="mt-1.5">
      <a
        href={href}
        onClick={(e) => {
          e.preventDefault()
          e.stopPropagation()
          onDocLinkClick(path)
        }}
        onKeyDown={(e) => e.stopPropagation()}
        className="inline-flex items-center gap-1 text-xs text-primary hover:underline focus-visible:underline focus-visible:outline-none"
        aria-label={`Open spec doc ${path}`}
        title={`Open spec doc: ${path}`}
      >
        <Link2 className="h-3 w-3" />
        <span className="font-mono truncate">{path}</span>
      </a>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Section C — docs/cockpit/*.md index
// ---------------------------------------------------------------------------

function DocSection({
  items,
  loading,
  searchQuery,
  onDocClick,
  onCardLinkClick,
}: {
  items: DocSpecItem[]
  loading: boolean
  searchQuery: string
  onDocClick: (path: string) => void
  onCardLinkClick: (cardId: string) => void
}) {
  return (
    <section aria-labelledby="doc-section-heading" className="space-y-3">
      <div className="flex items-center justify-between">
        <h2
          id="doc-section-heading"
          className="text-lg font-semibold flex items-center gap-2"
        >
          <BookText className="h-5 w-5" />
          From Cockpit Docs
        </h2>
        <Badge variant="secondary" className="text-xs">
          {items.length}
        </Badge>
      </div>
      <p className="text-sm text-muted-foreground">
        Decision &amp; spec docs from the platform's{' '}
        <code className="text-xs">docs/cockpit/</code> tree. Click a row
        to read the full doc.
      </p>
      {loading ? (
        <SectionEmpty>Loading docs…</SectionEmpty>
      ) : items.length === 0 ? (
        <SectionEmpty>
          {searchQuery ? 'No docs match your filter' : 'No docs in docs/cockpit/'}
        </SectionEmpty>
      ) : (
        <ul className="space-y-2">
          {items.map((doc) => (
            <li key={doc.path}>
              <Card
                className={CLICKABLE_CARD}
                onClick={() => onDocClick(doc.path)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    onDocClick(doc.path)
                  }
                }}
                tabIndex={0}
                role="button"
                aria-label={`Open doc ${doc.title}`}
              >
                <CardContent className="py-4">
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <FileText className="h-4 w-4 text-muted-foreground flex-shrink-0" />
                        <h3 className="font-medium truncate">{doc.title}</h3>
                      </div>
                      <p className="text-xs text-muted-foreground font-mono truncate">
                        {doc.path}
                      </p>
                      <ImplementedByChips
                        cards={doc.implemented_by ?? EMPTY_IMPLEMENTED_BY}
                        onCardLinkClick={onCardLinkClick}
                      />
                    </div>
                    <div className="flex flex-col items-end gap-1 flex-shrink-0">
                      <span
                        className="text-xs text-muted-foreground"
                        title={new Date(doc.modified_at).toLocaleString()}
                      >
                        {formatRelativeDate(doc.modified_at)}
                      </span>
                      <Badge variant="outline" className="text-xs">
                        {formatBytes(doc.size_bytes)}
                      </Badge>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

// Inline C-side "implemented by cards" chip-list (kanban plan
// 2026-07-28-plans-b-c-correlation Task 2). Renders only when the
// ``implemented_by`` array is non-empty (cards that claim this doc
// via ``metadata["spec_doc"] == path``). Each chip is an anchor with
// an ``href`` so it's keyboard-focusable + middle-click-open +
// copyable. Chips live inside a CLICKABLE_CARD — their ``onClick``
// MUST ``stopPropagation()`` so a chip click doesn't bubble up to the
// outer card's row destination (``/plans/<encoded-path>``) AND
// ``preventDefault()`` so the browser doesn't follow the ``href`` and
// hard-reload the SPA (review finding I1).
function ImplementedByChips({
  cards,
  onCardLinkClick,
}: {
  cards: CorrelatedCardItem[]
  onCardLinkClick: (cardId: string) => void
}) {
  if (cards.length === 0) return null
  return (
    <div className="mt-2">
      <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1.5">
        Implemented by cards
      </h4>
      <ul className="flex flex-wrap gap-1.5">
        {cards.map((card) => (
          <li key={card.card_id}>
            <a
              href={`/kanban?card=${card.card_id}`}
              onClick={(e) => {
                e.preventDefault()
                e.stopPropagation()
                onCardLinkClick(card.card_id)
              }}
              onKeyDown={(e) => e.stopPropagation()}
              className="inline-flex items-center gap-1 rounded-md border bg-secondary px-2 py-0.5 text-xs font-medium hover:bg-secondary/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label={`Open kanban card ${card.card_title}`}
              title={`Open kanban card: ${card.card_title}`}
            >
              <KanbanSquare className="h-3 w-3" />
              <span className="truncate max-w-[180px]">{card.card_title}</span>
            </a>
          </li>
        ))}
      </ul>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function SectionEmpty({ children }: { children: React.ReactNode }) {
  return (
    <Card>
      <CardContent className="py-6 text-center text-sm text-muted-foreground">
        {children}
      </CardContent>
    </Card>
  )
}

function filterCards(items: CardPlanItem[], query: string): CardPlanItem[] {
  const q = query.trim().toLowerCase()
  if (!q) return items
  return items.filter(
    (item) =>
      item.card_title.toLowerCase().includes(q) ||
      item.excerpt.toLowerCase().includes(q) ||
      (item.spec_doc?.toLowerCase().includes(q) ?? false)
  )
}

function filterDocs(items: DocSpecItem[], query: string): DocSpecItem[] {
  const q = query.trim().toLowerCase()
  if (!q) return items
  return items.filter(
    (doc) =>
      doc.title.toLowerCase().includes(q) ||
      doc.path.toLowerCase().includes(q)
  )
}

function formatRelativeDate(isoDate: string): string {
  const date = new Date(isoDate)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)
  const diffDays = Math.floor(diffMs / 86400000)

  if (diffMins < 1) return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  if (diffDays < 7) return `${diffDays}d ago`
  if (diffDays < 30) return `${Math.floor(diffDays / 7)}w ago`
  return date.toLocaleDateString()
}
