/**
 * Projects management page
 */
import { useState } from 'react';
import { FolderOpen, FolderPlus, Sparkles, ArrowRight } from 'lucide-react';
import { useProjectContext } from '@/contexts/ProjectContext';
import { ProjectList } from './ProjectList';
import { ProjectDiscovery } from './ProjectDiscovery';
import { AddProjectDialog } from './AddProjectDialog';
import { WachtrijSection } from './components/WachtrijSection';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button, buttonVariants } from '@/components/ui/button';
import { RefreshButton } from '@/components/shared/RefreshButton';

// Link to the canonical doc that explains the spec-driven new-project flow.
// The flow lives in `docs/cockpit/new-project-startup-flow.md` and is
// surfaced here so a user landing on this page knows there are TWO ways to
// get a project into the list: "birth" (a spec-driven `/new-app` interview)
// versus "track" (Add Folder for an existing directory).
// Read the full flow button. Keep the URL on `claude-cockpit` (not
// `agent-cockpit`) until the upstream GitHub repo is actually renamed:
// rebrand commit 60a097d swept this string with the brand sweep, but the
// `agent-cockpit` repo 404s and every other doc URL in the repo still
// points at `claude-cockpit`. See rebrand-decision §2.1 and §7.
const SPEC_DRIVEN_FLOW_DOC_URL =
  'https://github.com/guillaumevandevelde/claude-cockpit/blob/master/docs/cockpit/new-project-startup-flow.md';

export function ProjectsPage() {
  const { projects, loading, error, fetchProjects } = useProjectContext();
  const [showDiscovery, setShowDiscovery] = useState(false);
  const [showAddFolder, setShowAddFolder] = useState(false);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-2">
            <FolderOpen className="h-8 w-8" />
            Projects
          </h1>
          <p className="text-muted-foreground mt-2">
            Manage local project directories
          </p>
        </div>
        <div className="flex gap-2">
          <RefreshButton onClick={fetchProjects} loading={loading} />
          <Button variant="outline" onClick={() => setShowAddFolder(true)}>
            <FolderPlus className="h-4 w-4" />
            Add Folder
          </Button>
          <Button onClick={() => setShowDiscovery(!showDiscovery)}>
            {showDiscovery ? 'Hide Discovery' : 'Discover Projects'}
          </Button>
        </div>
      </div>

      {/* Discoverability hint — distinguishes "birth a new spec-driven project"
          (interview → design + plan → a fresh repo) from "track an existing
          folder" (Add Folder / Discover above). The birth route is cardless
          (kanban card d0531c12…): it runs as an interactive `/new-app`
          session, so there is no button here to click. See
          docs/cockpit/new-project-startup-flow.md §4. */}
      <Card className="border-amber-500/40 bg-amber-50/40 dark:bg-amber-950/20">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Sparkles className="h-5 w-5 text-amber-500" />
            Want to build a new app-idea, spec-driven?
          </CardTitle>
          <CardDescription>
            The &ldquo;Add Folder&rdquo; button above is for{' '}
            <em>tracking an existing folder</em>. To <em>birth a new
            spec-driven project</em>, run{' '}
            <code>/new-app</code> in an interactive Claude Code session: an
            interview turns your idea into a design-doc + TDD-plan, then
            births a fresh git-repo with seeded <code>.claude/</code>, the
            design and plan committed as repo files, and a first Backlog
            card. It appears in this list when it&apos;s done.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <a
            href={SPEC_DRIVEN_FLOW_DOC_URL}
            target="_blank"
            rel="noopener noreferrer"
            className={buttonVariants({ variant: 'outline' }) + ' gap-2'}
          >
            Read the full flow
            <ArrowRight className="h-4 w-4" />
          </a>
        </CardContent>
      </Card>

      {showDiscovery && (
        <ProjectDiscovery onProjectsDiscovered={() => {
          setShowDiscovery(false);
        }} />
      )}

      <AddProjectDialog open={showAddFolder} onOpenChange={setShowAddFolder} />

      {/* "Wacht op jou" — PO-facing aggregation of human-blocked items across
          all tracked projects. Sits above the project list so the highest-
          leverage action (answer a waiting question) is the first thing the
          PO sees on landing. See kanban card `c7ea21b0…`. */}
      <WachtrijSection />

      {error && (
        <Card className="border-destructive">
          <CardHeader>
            <CardTitle className="text-destructive">Error</CardTitle>
          </CardHeader>
          <CardContent>
            <p>{error}</p>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Tracked Projects</CardTitle>
          <CardDescription>
            {projects.length} project{projects.length !== 1 ? 's' : ''} tracked
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ProjectList projects={projects} loading={loading} />
        </CardContent>
      </Card>
    </div>
  );
}
