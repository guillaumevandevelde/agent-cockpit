import { CheckCircle2, XCircle, Circle, AlertTriangle } from "lucide-react";

export function pluralize(count: number, singular: string, plural?: string): string {
  return count === 1 ? singular : (plural ?? singular + "s");
}

export function getServerTypeLabel(type: string): string {
  switch (type) {
    case "stdio":
      return "Standard I/O";
    case "sse":
      return "Server-Sent Events";
    default:
      return "HTTP";
  }
}

export function getScopeBadgeVariant(
  scope: string
): "default" | "secondary" | "outline" | "destructive" {
  switch (scope) {
    case "managed":
      return "destructive";
    case "user":
      return "default";
    case "plugin":
      return "secondary";
    case "project":
      return "outline";
    default:
      return "outline";
  }
}

export function getScopeLabel(scope: string, source?: string): string {
  switch (scope) {
    case "managed":
      return "enforced";
    case "plugin":
      return source ? `plugin:${source}` : "plugin";
    default:
      return scope;
  }
}

export const STATUS_ICONS_LG = {
  connected: <CheckCircle2 className="h-5 w-5 text-green-500" />,
  failed: <XCircle className="h-5 w-5 text-red-500" />,
  "needs-auth": <AlertTriangle className="h-5 w-5 text-amber-500" />,
  "not-tested": <Circle className="h-5 w-5 text-muted-foreground" />,
} as const;

export const STATUS_COLORS = {
  connected: "text-green-600 dark:text-green-400",
  failed: "text-red-600 dark:text-red-400",
  "needs-auth": "text-amber-600 dark:text-amber-400",
  "not-tested": "text-muted-foreground",
} as const;
