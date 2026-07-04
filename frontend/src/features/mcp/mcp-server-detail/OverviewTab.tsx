import { AlertTriangle, CheckCircle2, AlertCircle, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { getServerStatus } from "../mcpStatus";
import { getServerTypeLabel, pluralize } from "./mcpServerHelpers";
import { AuthSection } from "./AuthSection";
import type {
  MCPServer,
  MCPTestConnectionResponse,
  MCPServerApprovalSettings,
} from "@/types/mcp";

// --- Overview Tab ---
export function OverviewTab({
  server,
  testResult,
  testing,
  onTest,
  onAuthComplete,
  approvalSettings,
  onApprovalChange,
}: {
  server: MCPServer;
  testResult: MCPTestConnectionResponse | null;
  testing: boolean;
  onTest: () => void;
  onAuthComplete: () => void;
  approvalSettings: MCPServerApprovalSettings | null;
  onApprovalChange: (serverName: string, mode: string | null) => void;
}) {
  const currentOverride = approvalSettings?.server_overrides.find(
    (o) => o.server_name === server.name
  );
  const currentMode = currentOverride?.mode ?? "default";

  const defaultLabel =
    approvalSettings?.default_mode === "always-allow"
      ? "Auto-approve"
      : approvalSettings?.default_mode === "always-deny"
        ? "Always deny"
        : "Ask each time";

  // Check capabilities for resource/prompt support info
  const capabilities = testResult?.capabilities ?? server.capabilities;
  const hasResources = !!capabilities?.resources;
  const hasPrompts = !!capabilities?.prompts;

  const status = getServerStatus(server, testResult);

  return (
    <ScrollArea className="h-[60vh]">
      <div className="space-y-4 pr-4">
        {/* Config display */}
        <div className="space-y-2">
          <h4 className="text-sm font-medium">Configuration</h4>
          <div className="text-sm space-y-1 bg-muted/30 rounded-md p-3">
            <div>
              <span className="font-medium">Type:</span>{" "}
              <span className="text-muted-foreground">
                {getServerTypeLabel(server.type)}
              </span>
            </div>
            {server.type === "stdio" && (
              <>
                <div>
                  <span className="font-medium">Command:</span>{" "}
                  <span className="text-muted-foreground font-mono">
                    {server.command}
                  </span>
                </div>
                {server.args && server.args.length > 0 && (
                  <div>
                    <span className="font-medium">Args:</span>{" "}
                    <span className="text-muted-foreground font-mono">
                      {server.args.join(" ")}
                    </span>
                  </div>
                )}
              </>
            )}
            {(server.type === "http" || server.type === "sse") && (
              <div>
                <span className="font-medium">URL:</span>{" "}
                <span className="text-muted-foreground font-mono break-all">
                  {server.url}
                </span>
              </div>
            )}
            {server.env && Object.keys(server.env).length > 0 && (
              <div>
                <span className="font-medium">Environment:</span>{" "}
                <span className="text-muted-foreground">
                  {Object.entries(server.env).map(([k, v]) => (
                    <span key={k} className="font-mono text-xs mr-2">
                      {k}={v}
                    </span>
                  ))}
                </span>
              </div>
            )}
            {server.headers && Object.keys(server.headers).length > 0 && (
              <div>
                <span className="font-medium">Headers:</span>{" "}
                <span className="text-muted-foreground">
                  {Object.keys(server.headers).length}{" "}
                  {pluralize(Object.keys(server.headers).length, "header")}
                </span>
              </div>
            )}
          </div>
        </div>

        {/* Connection info */}
        <div className="space-y-2">
          <h4 className="text-sm font-medium">Connection</h4>
          <div className="text-sm space-y-1 bg-muted/30 rounded-md p-3">
            {server.last_tested_at && (
              <div>
                <span className="font-medium">Last tested:</span>{" "}
                <span className="text-muted-foreground">
                  {new Date(server.last_tested_at).toLocaleString()}
                </span>
              </div>
            )}
            {server.mcp_server_name && (
              <div>
                <span className="font-medium">Server:</span>{" "}
                <span className="text-muted-foreground">
                  {server.mcp_server_name}
                  {server.mcp_server_version &&
                    ` v${server.mcp_server_version}`}
                </span>
              </div>
            )}
            {capabilities && (
              <div>
                <span className="font-medium">Capabilities:</span>{" "}
                <span className="text-muted-foreground">
                  {Object.keys(capabilities).join(", ") || "none"}
                </span>
              </div>
            )}
            {!capabilities && !server.last_tested_at && (
              <div className="text-muted-foreground text-xs">
                Not yet tested. Click Test to discover capabilities.
              </div>
            )}
            {capabilities && !hasResources && !hasPrompts && (
              <div className="text-muted-foreground text-xs mt-1">
                Server does not advertise resource or prompt support.
              </div>
            )}
          </div>
        </div>

        {/* Authentication (HTTP/SSE servers only) */}
        {(server.type === "http" || server.type === "sse") && (
          <AuthSection server={server} serverStatus={status.status} onAuthComplete={onAuthComplete} />
        )}

        {/* Per-server approval override */}
        {approvalSettings && (
          <div className="space-y-2">
            <h4 className="text-sm font-medium">Approval Override</h4>
            <div className="bg-muted/30 rounded-md p-3">
              <Label htmlFor="server-approval" className="text-sm">
                Tool approval mode for this server
              </Label>
              <Select
                value={currentMode}
                onValueChange={(value) =>
                  onApprovalChange(
                    server.name,
                    value === "default" ? null : value
                  )
                }
              >
                <SelectTrigger id="server-approval" className="mt-1">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="default">
                    Use default ({defaultLabel})
                  </SelectItem>
                  <SelectItem value="always-allow">Always allow</SelectItem>
                  <SelectItem value="always-deny">Always deny</SelectItem>
                  <SelectItem value="ask-every-time">
                    Ask every time
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        )}

        {/* Test Connection */}
        <div className="space-y-2">
          <Button
            onClick={onTest}
            disabled={testing}
            variant="outline"
            className="w-full"
          >
            <Play className="h-4 w-4 mr-2" />
            {testing ? "Testing..." : "Test Connection"}
          </Button>

          {testResult && (
            status.status === "needs-auth" ? (
              <Alert className="border-amber-500/50 bg-amber-50/50 dark:bg-amber-950/20 text-amber-800 dark:text-amber-200">
                <AlertTriangle className="h-4 w-4 text-amber-500" />
                <AlertDescription>
                  <div className="font-mono text-xs break-all">
                    {testResult.message}
                  </div>
                </AlertDescription>
              </Alert>
            ) : (
              <Alert variant={testResult.success ? "default" : "destructive"}>
                {testResult.success ? (
                  <CheckCircle2 className="h-4 w-4" />
                ) : (
                  <AlertCircle className="h-4 w-4" />
                )}
                <AlertDescription>
                  <div className="font-mono text-xs break-all">
                    {testResult.message}
                  </div>
                </AlertDescription>
              </Alert>
            )
          )}
        </div>
      </div>
    </ScrollArea>
  );
}
