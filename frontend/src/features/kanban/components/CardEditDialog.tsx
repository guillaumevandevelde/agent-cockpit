import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { MarkdownPreviewToggle } from "@/components/shared/MarkdownPreviewToggle";
import { MODAL_SIZES } from "@/lib/constants";
import { kanbanApi } from "../api";

const AUTO = "__auto__"; // sentinel: agent chosen by column default

export function CardEditDialog({
  open,
  initial,
  projectPath,
  onClose,
  onSubmit,
}: {
  open: boolean;
  initial?: { title: string; description: string; agent?: string | null };
  projectPath: string;
  onClose: () => void;
  onSubmit: (data: {
    title: string;
    description: string;
    agent: string | null;
  }) => void;
}) {
  const [title, setTitle] = useState(initial?.title ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [agent, setAgent] = useState(initial?.agent ?? AUTO);
  const [agents, setAgents] = useState<string[]>([]);

  useEffect(() => {
    if (!projectPath) return;
    kanbanApi
      .agents(projectPath)
      .then((r) => setAgents(r.agents))
      .catch(() => setAgents([]));
  }, [projectPath]);

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className={MODAL_SIZES.MD}>
        <DialogHeader>
          <DialogTitle>{initial ? "Edit card" : "New card"}</DialogTitle>
        </DialogHeader>
        <Input
          placeholder="Title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <MarkdownPreviewToggle value={description} onChange={setDescription} />
        <Select value={agent} onValueChange={setAgent}>
          <SelectTrigger className="w-full">
            <SelectValue placeholder="Agent" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={AUTO}>Auto (by column)</SelectItem>
            {agents.map((a) => (
              <SelectItem key={a} value={a}>
                {a}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={!title.trim()}
            onClick={() =>
              onSubmit({
                title,
                description,
                agent: agent === AUTO ? null : agent,
              })
            }
          >
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
