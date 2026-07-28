import { useState, useEffect, useCallback } from "react"
import {
  Server,
  Key,
  Plus,
  Trash2,
  Copy,
  Check,
  Shield,
  Eye,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Badge } from "@/components/ui/badge"
import { toast } from "sonner"
import { apiClient } from "@/lib/api"

interface MCPToken {
  id: number
  name: string
  scope: string
  agent_name: string | null
  enabled: boolean
  token_prefix: string
  last_used_at: string | null
  expires_at: string | null
  created_at: string | null
  revoked_at: string | null
}

interface TokenCreateResponse {
  id: number
  token: string
  name: string
  scope: string
}

export function MCPServerPage() {
  const [tokens, setTokens] = useState<MCPToken[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [newTokenName, setNewTokenName] = useState("")
  const [newTokenScope, setNewTokenScope] = useState("read")
  const [newTokenAgent, setNewTokenAgent] = useState("")
  const [createdToken, setCreatedToken] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const fetchTokens = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiClient<{ tokens: MCPToken[] }>(
        "mcp-server/tokens"
      )
      setTokens(data.tokens)
    } catch {
      toast.error("Failed to load tokens")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchTokens()
  }, [fetchTokens])

  const handleCreate = async () => {
    if (!newTokenName.trim()) {
      toast.error("Token name is required")
      return
    }
    try {
      const data = await apiClient<TokenCreateResponse>(
        "mcp-server/tokens",
        {
          method: "POST",
          body: JSON.stringify({
            name: newTokenName.trim(),
            scope: newTokenScope,
            agent_name: newTokenAgent.trim() || null,
          }),
        }
      )
      setCreatedToken(data.token)
      setNewTokenName("")
      setNewTokenScope("read")
      setNewTokenAgent("")
      setShowCreate(false)
      fetchTokens()
      toast.success("Token created")
    } catch {
      toast.error("Failed to create token")
    }
  }

  const handleRevoke = async (id: number) => {
    try {
      await apiClient(`mcp-server/tokens/${id}`, {
        method: "DELETE",
      })
      fetchTokens()
      toast.success("Token revoked")
    } catch {
      toast.error("Failed to revoke token")
    }
  }

  const copyToken = () => {
    if (createdToken) {
      navigator.clipboard.writeText(createdToken)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }

  const endpoint =
    typeof window !== "undefined"
      ? `${window.location.origin}/api/v1/mcp-server`
      : "/api/v1/mcp-server"

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Server className="h-6 w-6" />
          MCP Server
        </h1>
        <p className="text-muted-foreground mt-1">
          Expose Agent Cockpit data to AI agents via the Model Context Protocol.
        </p>
      </div>

      {/* Server Info */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Endpoint</CardTitle>
          <CardDescription>
            Configure your MCP client to connect to this endpoint.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2">
            <code className="flex-1 bg-muted px-3 py-2 rounded text-sm font-mono">
              {endpoint}
            </code>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                navigator.clipboard.writeText(endpoint)
                toast.success("Endpoint copied")
              }}
            >
              <Copy className="h-4 w-4" />
            </Button>
          </div>
          <div className="mt-4 text-sm text-muted-foreground">
            <p className="font-medium text-foreground mb-1">MCP Client Config:</p>
            <pre className="bg-muted p-3 rounded text-xs overflow-x-auto">
{`{
  "mcpServers": {
    "agent-cockpit": {
      "type": "http",
      "url": "${endpoint}",
      "headers": {
        "Authorization": "Bearer <your-token>"
      }
    }
  }
}`}
            </pre>
          </div>
        </CardContent>
      </Card>

      {/* Available Tools */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Available Tools</CardTitle>
          <CardDescription>
            Tools exposed via MCP for AI agents to call.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {[
              { name: "list_sessions", desc: "List all Claude Code sessions" },
              { name: "get_session", desc: "Get session details by ID" },
              { name: "list_scheduled_messages", desc: "List scheduled messages" },
              { name: "get_scheduled_message", desc: "Get scheduled message details" },
              { name: "list_mcp_servers", desc: "List configured MCP servers" },
              { name: "get_mcp_server", desc: "Get MCP server details" },
              { name: "get_config", desc: "Get merged Claude Code config" },
              { name: "list_config_files", desc: "List all config files" },
              { name: "list_projects", desc: "List registered projects" },
            ].map((tool) => (
              <div
                key={tool.name}
                className="flex items-start gap-2 p-2 rounded bg-muted/50"
              >
                <code className="text-xs font-mono text-primary shrink-0">
                  {tool.name}
                </code>
                <span className="text-xs text-muted-foreground">
                  {tool.desc}
                </span>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Tokens */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <div>
            <CardTitle className="text-lg flex items-center gap-2">
              <Key className="h-5 w-5" />
              Access Tokens
            </CardTitle>
            <CardDescription>
              Create tokens to authenticate MCP clients.
            </CardDescription>
          </div>
          <Button size="sm" onClick={() => setShowCreate(!showCreate)}>
            <Plus className="h-4 w-4 mr-1" />
            New Token
          </Button>
        </CardHeader>
        <CardContent>
          {/* Created token alert */}
          {createdToken && (
            <div className="mb-4 p-3 bg-green-50 dark:bg-green-950 border border-green-200 dark:border-green-800 rounded-lg">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-green-800 dark:text-green-200">
                    Token created! Copy it now — it won't be shown again.
                  </p>
                  <code className="block mt-1 text-xs font-mono break-all text-green-700 dark:text-green-300">
                    {createdToken}
                  </code>
                </div>
                <div className="flex gap-1 shrink-0 ml-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={copyToken}
                  >
                    {copied ? (
                      <Check className="h-4 w-4" />
                    ) : (
                      <Copy className="h-4 w-4" />
                    )}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setCreatedToken(null)}
                  >
                    Dismiss
                  </Button>
                </div>
              </div>
            </div>
          )}

          {/* Create form */}
          {showCreate && (
            <div className="mb-4 p-4 border rounded-lg space-y-3">
              <Input
                placeholder="Token name (e.g. 'cursor-agent')"
                value={newTokenName}
                onChange={(e) => setNewTokenName(e.target.value)}
              />
              <div className="flex gap-2">
                <Select value={newTokenScope} onValueChange={setNewTokenScope}>
                  <SelectTrigger className="w-[180px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="read">
                      <span className="flex items-center gap-1">
                        <Eye className="h-3 w-3" /> Read only
                      </span>
                    </SelectItem>
                    <SelectItem value="write">
                      <span className="flex items-center gap-1">
                        <Shield className="h-3 w-3" /> Read + Write
                      </span>
                    </SelectItem>
                  </SelectContent>
                </Select>
                <Input
                  placeholder="Agent name (optional)"
                  value={newTokenAgent}
                  onChange={(e) => setNewTokenAgent(e.target.value)}
                  className="flex-1"
                />
              </div>
              <div className="flex gap-2">
                <Button size="sm" onClick={handleCreate}>
                  Create
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setShowCreate(false)}
                >
                  Cancel
                </Button>
              </div>
            </div>
          )}

          {/* Token list */}
          {loading ? (
            <p className="text-sm text-muted-foreground">Loading...</p>
          ) : tokens.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No tokens yet. Create one to get started.
            </p>
          ) : (
            <div className="space-y-2">
              {tokens.map((t) => (
                <div
                  key={t.id}
                  className="flex items-center justify-between p-3 border rounded-lg"
                >
                  <div className="flex items-center gap-3">
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-sm">{t.name}</span>
                        {t.agent_name && (
                          <span className="text-muted-foreground text-xs">
                            ({t.agent_name})
                          </span>
                        )}
                        <Badge variant={t.scope === "write" ? "default" : "secondary"}>
                          {t.scope}
                        </Badge>
                      </div>
                      <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground">
                        <code className="font-mono">{t.token_prefix}...</code>
                        {t.revoked_at ? (
                          <Badge variant="destructive">Revoked</Badge>
                        ) : t.enabled ? (
                          <span className="text-green-600">Active</span>
                        ) : (
                          <span>Disabled</span>
                        )}
                        {t.last_used_at && (
                          <span>Last used: {new Date(t.last_used_at).toLocaleDateString()}</span>
                        )}
                      </div>
                    </div>
                  </div>
                  {!t.revoked_at && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleRevoke(t.id)}
                    >
                      <Trash2 className="h-4 w-4 text-destructive" />
                    </Button>
                  )}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
