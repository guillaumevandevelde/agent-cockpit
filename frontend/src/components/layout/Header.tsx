import { Terminal, Radio, AlertCircle, Server, Menu } from "lucide-react";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { AttentionToggle } from "@/components/ui/attention-toggle";
import { Badge } from "@/components/ui/badge";
import { CockpitLogo } from "@/components/shared/CockpitLogo";
import { useSystemStatus } from "@/hooks/useSystemStatus";
import { useInstanceDocumentTitle } from "@/hooks/useInstanceDocumentTitle";
import { useProviderContext } from "@/contexts/ProviderContext";
import { useSidebar } from "@/contexts/SidebarContext";
import { getInstanceAccentClasses } from "@/lib/instanceAccent";
import { cn } from "@/lib/utils";

export function Header() {
  const status = useSystemStatus();
  const instance = status?.instance ?? null;
  const accentClasses = getInstanceAccentClasses(instance?.accent);
  const { providers, selectedProviderId, selectedProvider } = useProviderContext();
  const { setMobileOpen } = useSidebar();
  const providerStatuses = providers
    .map((provider) => status?.providers?.[provider.id] ?? provider)
    .filter((provider) => provider.installed || provider.id === selectedProviderId);
  useInstanceDocumentTitle(instance);

  const instanceTitle = instance
    ? [
        `Agent Cockpit instance: ${instance.name}`,
        `Hostname: ${instance.hostname}`,
        `Opened from: ${typeof window === "undefined" ? "unknown" : window.location.host}`,
      ].join("\n")
    : undefined;

  if (providerStatuses.length === 0 && selectedProvider) {
    providerStatuses.push(status?.providers?.[selectedProviderId] ?? selectedProvider);
  }

  return (
    <header className={cn("border-b bg-background", instance && "border-t-4", instance && accentClasses.headerBorder)}>
      <div className="flex h-16 items-center justify-between px-6">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => setMobileOpen(true)}
            className="sm:hidden -ml-1 inline-flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground"
            aria-label="Open menu"
          >
            <Menu className="h-5 w-5" />
          </button>
          <CockpitLogo className="h-10 w-10 text-primary" />
          <div>
            <h1 className="text-2xl font-bold text-primary leading-tight">Agent Cockpit</h1>
            <p className="text-xs text-muted-foreground">Mission control for your local agents</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {status && (
            <>
              {instance && (
                <Badge
                  variant="outline"
                  className={cn("gap-1 text-xs max-w-[12rem] truncate", accentClasses.badge)}
                  title={instanceTitle}
                >
                  <Server className="h-3 w-3 shrink-0" />
                  <span className={cn("h-2 w-2 rounded-full shrink-0", accentClasses.dot)} />
                  <span className="truncate">{instance.name}</span>
                </Badge>
              )}
              {providerStatuses.map((provider) => (
                <Badge
                  key={provider.id}
                  variant="outline"
                  className={cn(
                    "gap-1 text-xs",
                    provider.installed ? "font-mono" : "border-destructive/40 text-destructive"
                  )}
                >
                  {provider.installed ? (
                    <Terminal className="h-3 w-3" />
                  ) : (
                    <AlertCircle className="h-3 w-3" />
                  )}
                  {provider.display_name}
                  {provider.version ? ` v${provider.version}` : provider.installed ? " ready" : " missing"}
                </Badge>
              ))}
              <Badge
                variant="secondary"
                className={cn(
                  "gap-1 text-xs",
                  status.activeSessions > 0 && "bg-green-500/15 text-green-600 dark:text-green-400"
                )}
              >
                <Radio className="h-3 w-3" />
                {status.activeSessions} active
              </Badge>
            </>
          )}
          <AttentionToggle />
          <ThemeToggle />
        </div>
      </div>
    </header>
  )
}
