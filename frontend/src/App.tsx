import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Toaster } from 'sonner'
import { ProjectProvider } from './contexts/ProjectContext'
import { DashboardProvider } from './contexts/DashboardContext'
import { ProviderProvider } from './contexts/ProviderContext'
import { AttentionProvider } from './contexts/AttentionContext'
import { MainLayout } from './components/layout/MainLayout'
const DashboardPage = lazy(() => import('./features/dashboard/DashboardPage').then((m) => ({ default: m.DashboardPage })))
const ConfigViewerPage = lazy(() => import('./features/config/ConfigViewerPage').then((m) => ({ default: m.ConfigViewerPage })))
const ProjectsPage = lazy(() => import('./features/projects/ProjectsPage').then((m) => ({ default: m.ProjectsPage })))
const MCPServersPage = lazy(() => import('./features/mcp/MCPServersPage').then((m) => ({ default: m.MCPServersPage })))
const CommandsPage = lazy(() => import('./features/commands/CommandsPage').then((m) => ({ default: m.CommandsPage })))
const PluginsPage = lazy(() => import('./features/plugins/PluginsPage').then((m) => ({ default: m.PluginsPage })))
const HooksPage = lazy(() => import('./features/hooks/HooksPage').then((m) => ({ default: m.HooksPage })))
const PermissionsPage = lazy(() => import('./features/permissions/PermissionsPage').then((m) => ({ default: m.PermissionsPage })))
const AgentsPage = lazy(() => import('./features/agents/AgentsPage').then((m) => ({ default: m.AgentsPage })))
const SkillsPage = lazy(() => import('./features/skills/SkillsPage').then((m) => ({ default: m.SkillsPage })))
const BackupPage = lazy(() => import('./features/backup/BackupPage').then((m) => ({ default: m.BackupPage })))
const OutputStylesPage = lazy(() => import('./features/output-styles/OutputStylesPage').then((m) => ({ default: m.OutputStylesPage })))
const StatusLinePage = lazy(() => import('./features/statusline/StatusLinePage').then((m) => ({ default: m.StatusLinePage })))
const SessionsPage = lazy(() => import('./features/sessions/SessionsPage').then((m) => ({ default: m.SessionsPage })))
const SessionViewPage = lazy(() => import('./features/sessions/SessionViewPage').then((m) => ({ default: m.SessionViewPage })))
const UsagePage = lazy(() => import('./features/usage/UsagePage').then((m) => ({ default: m.UsagePage })))
const MemoryPage = lazy(() => import('./features/memory/MemoryPage').then((m) => ({ default: m.MemoryPage })))
const ContextPage = lazy(() => import('./features/context/ContextPage').then((m) => ({ default: m.ContextPage })))
const PlansPage = lazy(() => import('./features/plans/PlansPage').then((m) => ({ default: m.PlansPage })))
const PlanDetailPage = lazy(() => import('./features/plans/PlanDetailPage').then((m) => ({ default: m.PlanDetailPage })))
const CCBridgePage = lazy(() => import('./features/cc-bridge/CCBridgePage').then((m) => ({ default: m.CCBridgePage })))
const PresencePage = lazy(() => import('./features/presence/PresencePage').then((m) => ({ default: m.PresencePage })))
const AgentMailPage = lazy(() => import('./features/agent-mail/AgentMailPage').then((m) => ({ default: m.AgentMailPage })))
const KanbanPage = lazy(() => import('./features/kanban/KanbanPage').then((m) => ({ default: m.default })))
const ImpedimentPage = lazy(() => import('./features/kanban/ImpedimentPage').then((m) => ({ default: m.ImpedimentPage })))
const ApmPage = lazy(() => import('./features/apm/ApmPage').then((m) => ({ default: m.ApmPage })))
const MCPServerPage = lazy(() => import('./features/mcp-server/MCPServerPage').then((m) => ({ default: m.MCPServerPage })))
const AgentPerformancePage = lazy(() => import('./features/agent-performance/AgentPerformancePage').then((m) => ({ default: m.AgentPerformancePage })))
const SandcastlePage = lazy(() => import('./features/sandcastle/SandcastlePage').then((m) => ({ default: m.SandcastlePage })))
const HostsPage = lazy(() => import('./features/hosts/HostsPage').then((m) => ({ default: m.HostsPage })))
const SubscriptionsPage = lazy(() => import('./features/subscriptions/SubscriptionsPage').then((m) => ({ default: m.SubscriptionsPage })))
const BlueprintsPage = lazy(() => import('./features/blueprints').then((m) => ({ default: m.BlueprintsPage })))
const PortfolioPage = lazy(() => import('./features/portfolio/PortfolioPage').then((m) => ({ default: m.PortfolioPage })))
const SecurityProfilePage = lazy(() => import('./features/security/SecurityProfilePage').then((m) => ({ default: m.SecurityProfilePage })))
const EndpointsPage = lazy(() => import('./features/endpoints/EndpointsPage').then((m) => ({ default: m.EndpointsPage })))

function App() {
  return (
    <ProjectProvider>
      <ProviderProvider>
        <DashboardProvider>
          <AttentionProvider>
          <BrowserRouter>
            <Toaster richColors position="top-right" />
            <Suspense fallback={<div className="p-6 text-sm text-muted-foreground">Loading...</div>}>
            <Routes>
              <Route path="/" element={<MainLayout />}>
                <Route index element={<DashboardPage />} />
                <Route path="config" element={<ConfigViewerPage />} />
                <Route path="mcp" element={<MCPServersPage />} />
                <Route path="commands" element={<CommandsPage />} />
                <Route path="plugins" element={<PluginsPage />} />
                <Route path="hooks" element={<HooksPage />} />
                <Route path="permissions" element={<PermissionsPage />} />
                <Route path="agents" element={<AgentsPage />} />
                <Route path="skills" element={<SkillsPage />} />
                <Route path="memory" element={<MemoryPage />} />
                <Route path="projects" element={<ProjectsPage />} />
                <Route path="backup" element={<BackupPage />} />
                <Route path="output-styles" element={<OutputStylesPage />} />
                <Route path="statusline" element={<StatusLinePage />} />
                <Route path="sessions/:projectFolder/:sessionId" element={<SessionViewPage />} />
                <Route path="sessions" element={<SessionsPage />} />
                <Route path="agent-bridge" element={<CCBridgePage />} />
                <Route path="cc-bridge" element={<CCBridgePage />} />
                <Route path="presence" element={<PresencePage />} />
                <Route path="agent-mail" element={<AgentMailPage />} />
                <Route path="plans/:filename" element={<PlanDetailPage />} />
                <Route path="plans" element={<PlansPage />} />
                <Route path="context" element={<ContextPage />} />
                <Route path="usage" element={<UsagePage />} />
                <Route path="kanban" element={<KanbanPage />} />
                <Route path="kanban/impediment/:cardId" element={<ImpedimentPage />} />
                <Route path="apm" element={<ApmPage />} />
                <Route path="agent-performance" element={<AgentPerformancePage />} />
                <Route path="mcp-server" element={<MCPServerPage />} />
                <Route path="sandcastle" element={<SandcastlePage />} />
                <Route path="hosts" element={<HostsPage />} />
                <Route path="subscriptions" element={<SubscriptionsPage />} />
                <Route path="blueprints" element={<BlueprintsPage />} />
                <Route path="portfolio" element={<PortfolioPage />} />
                <Route path="security" element={<SecurityProfilePage />} />
                <Route path="endpoints" element={<EndpointsPage />} />
              </Route>
            </Routes>
            </Suspense>
          </BrowserRouter>
          </AttentionProvider>
        </DashboardProvider>
      </ProviderProvider>
    </ProjectProvider>
  )
}

export default App
