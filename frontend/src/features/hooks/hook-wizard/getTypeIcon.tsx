import { Terminal, MessageSquare, Bot } from "lucide-react";
import type { HookType } from "@/types/hooks";

export const getTypeIcon = (t: HookType) => {
  switch (t) {
    case "command":
      return <Terminal className="h-4 w-4" />;
    case "prompt":
      return <MessageSquare className="h-4 w-4" />;
    case "agent":
      return <Bot className="h-4 w-4" />;
  }
};
