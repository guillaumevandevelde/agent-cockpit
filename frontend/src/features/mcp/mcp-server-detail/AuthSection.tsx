import { useState, useEffect, useRef, useCallback } from "react";
import {
  CheckCircle2,
  XCircle,
  AlertTriangle,
  KeyRound,
  Loader2,
  ExternalLink,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { apiClient } from "@/lib/api";
import { toast } from "sonner";
import type { MCPConnectionStatus } from "../mcpStatus";
import type { MCPServer, MCPAuthStatus, MCPAuthStartResponse } from "@/types/mcp";

// --- Authentication Section (HTTP/SSE servers only) ---
export function AuthSection({
  server,
  serverStatus,
  onAuthComplete,
}: {
  server: MCPServer;
  serverStatus: MCPConnectionStatus;
  onAuthComplete: () => void;
}) {
  const [authStatus, setAuthStatus] = useState<MCPAuthStatus | null>(null);
  const [authenticating, setAuthenticating] = useState(false);
  const [loading, setLoading] = useState(true);
  const popupRef = useRef<Window | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchAuthStatus = useCallback(async () => {
    try {
      const status = await apiClient<MCPAuthStatus>(
        `mcp/servers/${encodeURIComponent(server.name)}/auth-status?scope=${server.scope}`
      );
      setAuthStatus(status);
      return status;
    } catch {
      setAuthStatus(null);
      return null;
    } finally {
      setLoading(false);
    }
  }, [server.name, server.scope]);

  useEffect(() => {
    fetchAuthStatus();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [fetchAuthStatus]);

  // Listen for postMessage from OAuth callback popup
  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (event.data?.type === "mcp-oauth-complete") {
        setAuthenticating(false);
        if (pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
        fetchAuthStatus().then(() => onAuthComplete());
        toast.success("Authentication successful!");
      }
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, [fetchAuthStatus, onAuthComplete]);

  const handleAuthenticate = async () => {
    setAuthenticating(true);
    try {
      const response = await apiClient<MCPAuthStartResponse>(
        `mcp/servers/${encodeURIComponent(server.name)}/auth/start?scope=${server.scope}`,
        { method: "POST" }
      );

      // Open OAuth URL in popup
      popupRef.current = window.open(
        response.auth_url,
        "mcp-oauth",
        "width=600,height=700,popup=yes"
      );

      // Poll auth status as fallback (in case postMessage doesn't work)
      pollRef.current = setInterval(async () => {
        const status = await fetchAuthStatus();
        if (status?.has_token && !status.expired) {
          setAuthenticating(false);
          if (pollRef.current) {
            clearInterval(pollRef.current);
            pollRef.current = null;
          }
          if (popupRef.current && !popupRef.current.closed) {
            popupRef.current.close();
          }
          onAuthComplete();
          toast.success("Authentication successful!");
        }
        // Also stop if popup was closed without completing
        if (popupRef.current?.closed) {
          setAuthenticating(false);
          if (pollRef.current) {
            clearInterval(pollRef.current);
            pollRef.current = null;
          }
          fetchAuthStatus();
        }
      }, 2000);
    } catch (error) {
      setAuthenticating(false);
      const message = error instanceof Error ? error.message : String(error);
      toast.error(`OAuth failed: ${message}`);
    }
  };

  if (loading) {
    return (
      <div className="space-y-2">
        <h4 className="text-sm font-medium">Authentication</h4>
        <div className="bg-muted/30 rounded-md p-3 flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Checking auth status...
        </div>
      </div>
    );
  }

  const hasToken = authStatus?.has_token ?? false;
  const expired = authStatus?.expired ?? false;
  const hasClientReg = authStatus?.has_client_registration ?? false;

  // If the server status says "needs-auth" despite having a stored token,
  // the token is invalid/revoked. Also handle "connected" as truly authenticated.
  const tokenInvalid = hasToken && serverStatus === "needs-auth";
  const isConnected = serverStatus === "connected";

  // Determine display state
  let statusIcon: React.ReactNode;
  let statusText: string;
  let needsAuth: boolean;

  if (isConnected && hasToken) {
    statusIcon = <CheckCircle2 className="h-4 w-4 text-green-500" />;
    statusText = "Authenticated";
    needsAuth = false;
  } else if (tokenInvalid) {
    statusIcon = <XCircle className="h-4 w-4 text-red-500" />;
    statusText = "Token not working";
    needsAuth = true;
  } else if (hasToken && expired) {
    statusIcon = <AlertTriangle className="h-4 w-4 text-amber-500" />;
    statusText = "Token expired";
    needsAuth = true;
  } else if (hasToken && !expired) {
    statusIcon = <CheckCircle2 className="h-4 w-4 text-green-500" />;
    statusText = "Token stored";
    needsAuth = false;
  } else if (hasClientReg) {
    statusIcon = <KeyRound className="h-4 w-4 text-amber-500" />;
    statusText = "Registered, needs login";
    needsAuth = true;
  } else {
    statusIcon = <KeyRound className="h-4 w-4 text-muted-foreground" />;
    statusText = "Not authenticated";
    needsAuth = true;
  }

  return (
    <div className="space-y-2">
      <h4 className="text-sm font-medium">Authentication</h4>
      <div className="bg-muted/30 rounded-md p-3 space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm">
            {statusIcon}
            <span className={
              isConnected && hasToken ? "text-green-700 dark:text-green-400 font-medium" :
              tokenInvalid ? "text-red-700 dark:text-red-400 font-medium" :
              expired ? "text-amber-700 dark:text-amber-400 font-medium" :
              hasToken ? "text-green-700 dark:text-green-400 font-medium" :
              hasClientReg ? "text-amber-700 dark:text-amber-400 font-medium" :
              "text-muted-foreground"
            }>
              {statusText}
            </span>
          </div>
          <Button
            size="sm"
            variant={needsAuth ? "default" : "outline"}
            onClick={handleAuthenticate}
            disabled={authenticating}
          >
            {authenticating ? (
              <>
                <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                Waiting...
              </>
            ) : (
              <>
                <ExternalLink className="h-3 w-3 mr-1" />
                {needsAuth ? "Authenticate" : "Re-authenticate"}
              </>
            )}
          </Button>
        </div>
        {authenticating && (
          <p className="text-xs text-muted-foreground">
            Complete authentication in the popup window. This page will update automatically.
          </p>
        )}
      </div>
    </div>
  );
}
