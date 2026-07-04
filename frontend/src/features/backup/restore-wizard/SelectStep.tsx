import { Badge } from "@/components/ui/badge";
import { AlertTriangle, Download, Monitor, Package, Puzzle, Server } from "lucide-react";
import {
  type Backup,
  type RestorePlan,
  formatBytes,
  formatDate,
  PLATFORM_NAMES,
} from "@/types/backup";

interface SelectStepProps {
  backup: Backup;
  plan: RestorePlan | null;
}

export function SelectStep({ backup, plan }: SelectStepProps) {
  return (
    <div className="space-y-4">
      <div className="p-4 bg-muted rounded-lg">
        <div className="grid grid-cols-2 gap-3 text-sm">
          <span className="text-muted-foreground">Name:</span>
          <span className="font-medium">{backup.name}</span>
          <span className="text-muted-foreground">Scope:</span>
          <span className="font-medium capitalize">{backup.scope}</span>
          <span className="text-muted-foreground">Size:</span>
          <span className="font-medium">{formatBytes(backup.size_bytes)}</span>
          <span className="text-muted-foreground">Created:</span>
          <span className="font-medium">{formatDate(backup.created_at)}</span>
        </div>
      </div>

      {plan && (
        <>
          {/* Platform info */}
          <div className="flex items-center gap-2 p-3 bg-muted/50 rounded-lg">
            <Monitor className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm">
              Created on{" "}
              <span className="font-medium">
                {PLATFORM_NAMES[plan.platform_backup] || plan.platform_backup}
              </span>
            </span>
            {!plan.platform_compatible && (
              <Badge variant="outline" className="ml-auto bg-amber-50 text-amber-700 border-amber-200">
                <AlertTriangle className="h-3 w-3 mr-1" />
                Different OS
              </Badge>
            )}
          </div>

          {/* Warnings */}
          {plan.warnings.map((warning, i) => (
            <div
              key={i}
              className={`flex items-start gap-2 p-3 rounded-lg ${
                warning.severity === "error"
                  ? "bg-red-50 border border-red-200"
                  : "bg-amber-50 border border-amber-200"
              }`}
            >
              <AlertTriangle
                className={`h-4 w-4 mt-0.5 ${
                  warning.severity === "error" ? "text-red-600" : "text-amber-600"
                }`}
              />
              <span className="text-sm">{warning.message}</span>
            </div>
          ))}

          {/* Summary badges */}
          <div className="flex flex-wrap gap-2">
            {plan.skills_to_restore.length > 0 && (
              <Badge variant="secondary">
                <Package className="h-3 w-3 mr-1" />
                {plan.skills_to_restore.length} Skills
              </Badge>
            )}
            {plan.plugins_to_restore.length > 0 && (
              <Badge variant="secondary">
                <Puzzle className="h-3 w-3 mr-1" />
                {plan.plugins_to_restore.length} Plugins
              </Badge>
            )}
            {plan.mcp_servers_to_restore.length > 0 && (
              <Badge variant="secondary">
                <Server className="h-3 w-3 mr-1" />
                {plan.mcp_servers_to_restore.length} MCP Servers
              </Badge>
            )}
            {plan.has_dependencies && (
              <Badge variant="outline" className="bg-blue-50 text-blue-700 border-blue-200">
                <Download className="h-3 w-3 mr-1" />
                Has dependencies
              </Badge>
            )}
          </div>
        </>
      )}
    </div>
  );
}
