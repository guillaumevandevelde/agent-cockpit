import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, FileText, Calendar, HardDrive } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { MarkdownRenderer } from '@/components/shared/MarkdownRenderer'
import { RefreshButton } from '@/components/shared/RefreshButton'
import { usePlansApi } from '@/hooks/usePlansApi'
import { useFetchData } from '@/hooks/useFetchData'
import { formatBytes } from '@/types/backup'
import type { DocContentResponse } from '@/types/plans'

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
 */
export function PlanDetailPage() {
  const { filename } = useParams<{ filename: string }>()
  const navigate = useNavigate()
  const { getDocContent } = usePlansApi()

  const { data: doc, loading, error, refresh: fetchDoc } = useFetchData<DocContentResponse>(
    () => (filename ? getDocContent(filename) : Promise.resolve(null as unknown as DocContentResponse)),
    [filename, getDocContent]
  )

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
    </div>
  )
}