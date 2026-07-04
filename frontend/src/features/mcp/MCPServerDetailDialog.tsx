import { useState } from "react";
import { Edit2, Trash2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { MODAL_SIZES } from "@/lib/constants";
import { apiClient } from "@/lib/api";
import { toast } from "sonner";
import { getServerStatus } from "./mcpStatus";
import {
  getScopeBadgeVariant,
  getScopeLabel,
  getServerTypeLabel,
  pluralize,
  STATUS_ICONS_LG,
  STATUS_COLORS,
} from "./mcp-server-detail/mcpServerHelpers";
import { OverviewTab } from "./mcp-server-detail/OverviewTab";
import { ToolsTab } from "./mcp-server-detail/ToolsTab";
import { ResourcesTab } from "./mcp-server-detail/ResourcesTab";
import { PromptsTab } from "./mcp-server-detail/PromptsTab";
import type {
  MCPServer,
  MCPTestConnectionResponse,
  MCPServerApprovalSettings,
} from "@/types/mcp";

interface MCPServerDetailDialogProps {
  server: MCPServer | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onEdit: (server: MCPServer) => void;
  onDelete: (name: string, scope: string) => void;
  onTestComplete: () => void;
  approvalSettings: MCPServerApprovalSettings | null;
  onApprovalChange: (serverName: string, mode: string | null) => void;
  readOnly?: boolean;
}

// --- Main Dialog ---
export function MCPServerDetailDialog({
  server,
  open,
  onOpenChange,
  onEdit,
  onDelete,
  onTestComplete,
  approvalSettings,
  onApprovalChange,
  readOnly = false,
}: MCPServerDetailDialogProps) {
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] =
    useState<MCPTestConnectionResponse | null>(null);

  if (!server) return null;

  const isEditable =
    !readOnly &&
    server.scope !== "plugin" &&
    server.scope !== "managed";

  const toolCount = server.tool_count ?? server.tools?.length ?? 0;
  const resourceCount = server.resource_count ?? server.resources?.length ?? 0;
  const promptCount = server.prompt_count ?? server.prompts?.length ?? 0;

  const handleTest = async () => {
    if (!server) return;
    setTesting(true);
    setTestResult(null);
    try {
      const response = await apiClient<MCPTestConnectionResponse>(
        `mcp/servers/${encodeURIComponent(server.name)}/test?scope=${server.scope}`,
        { method: "POST" }
      );
      if (response && typeof response.success === "boolean") {
        setTestResult(response);
        if (response.success) {
          const responseToolCount = response.tools?.length || 0;
          toast.success(
            `Connected! ${responseToolCount} ${pluralize(responseToolCount, "tool")} available`
          );
          onTestComplete();
        } else {
          toast.error("Connection failed");
        }
      } else {
        setTestResult({
          success: false,
          message: "Invalid response from server",
        });
        toast.error("Invalid response from server");
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setTestResult({
        success: false,
        message: `Failed to test connection: ${message}`,
      });
      toast.error(`Test failed: ${message}`);
    } finally {
      setTesting(false);
    }
  };

  const handleDelete = () => {
    if (
      confirm(
        `Are you sure you want to delete the MCP server "${server.name}"?`
      )
    ) {
      onDelete(server.name, server.scope);
      onOpenChange(false);
    }
  };

  // Merge test result counts with server counts for tab badges
  const displayToolCount =
    testResult?.tools?.length ?? toolCount;
  const displayResourceCount =
    testResult?.resource_count ?? resourceCount;
  const displayPromptCount =
    testResult?.prompt_count ?? promptCount;

  const status = getServerStatus(server, testResult);

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) setTestResult(null);
        onOpenChange(o);
      }}
    >
      <DialogContent className={`${MODAL_SIZES.LG} flex flex-col`}>
        <DialogHeader>
          <div className="flex items-center gap-3">
            {STATUS_ICONS_LG[status.status]}
            <div className="flex-1 min-w-0">
              <DialogTitle className="text-lg truncate">
                {server.name}
              </DialogTitle>
              <div className="flex items-center gap-2 mt-1">
                <span className="text-xs text-muted-foreground">
                  {getServerTypeLabel(server.type)}
                </span>
                <Badge variant={getScopeBadgeVariant(server.scope)} className="text-xs">
                  {getScopeLabel(server.scope, server.source)}
                </Badge>
                <span className={`text-xs font-medium ${STATUS_COLORS[status.status]}`}>
                  {status.label}
                </span>
              </div>
            </div>
          </div>
        </DialogHeader>

        <Tabs defaultValue="overview" className="flex-1">
          <TabsList className="w-full justify-start">
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="tools" className="gap-1.5">
              Tools
              {displayToolCount > 0 && (
                <Badge variant="secondary" className="text-[10px] px-1.5 py-0 ml-1">
                  {displayToolCount}
                </Badge>
              )}
            </TabsTrigger>
            <TabsTrigger value="resources" className="gap-1.5">
              Resources
              {displayResourceCount > 0 && (
                <Badge variant="secondary" className="text-[10px] px-1.5 py-0 ml-1">
                  {displayResourceCount}
                </Badge>
              )}
            </TabsTrigger>
            <TabsTrigger value="prompts" className="gap-1.5">
              Prompts
              {displayPromptCount > 0 && (
                <Badge variant="secondary" className="text-[10px] px-1.5 py-0 ml-1">
                  {displayPromptCount}
                </Badge>
              )}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="overview">
            <OverviewTab
              server={server}
              testResult={testResult}
              testing={testing}
              onTest={handleTest}
              onAuthComplete={() => {
                handleTest();
              }}
              approvalSettings={approvalSettings}
              onApprovalChange={onApprovalChange}
            />
          </TabsContent>

          <TabsContent value="tools">
            <ToolsTab server={server} testResult={testResult} />
          </TabsContent>

          <TabsContent value="resources">
            <ResourcesTab server={server} testResult={testResult} />
          </TabsContent>

          <TabsContent value="prompts">
            <PromptsTab server={server} testResult={testResult} />
          </TabsContent>
        </Tabs>

        {/* Footer */}
        <div className="flex justify-between pt-4 border-t">
          <div className="flex gap-2">
            {isEditable && (
              <>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    onEdit(server);
                    onOpenChange(false);
                  }}
                >
                  <Edit2 className="h-4 w-4 mr-1" />
                  Edit
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="text-destructive hover:text-destructive"
                  onClick={handleDelete}
                >
                  <Trash2 className="h-4 w-4 mr-1" />
                  Delete
                </Button>
              </>
            )}
          </div>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
