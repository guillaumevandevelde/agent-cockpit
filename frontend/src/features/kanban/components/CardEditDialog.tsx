import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { MarkdownPreviewToggle } from "@/components/shared/MarkdownPreviewToggle";
import { MODAL_SIZES } from "@/lib/constants";
import { PRIORITIES, type Priority } from "../types";

function parseLabels(raw: string): string[] {
  return raw
    .split(",")
    .map((l) => l.trim())
    .filter(Boolean);
}

export function CardEditDialog({
  open,
  initial,
  columns,
  defaultAgent,
  onClose,
  onSubmit,
}: {
  open: boolean;
  initial?: {
    title: string;
    description: string;
    priority?: string | null;
    labels?: string[] | null;
  };
  columns: string[];
  defaultAgent?: string | null;
  onClose: () => void;
  onSubmit: (data: {
    title: string;
    description: string;
    column: string;
    priority: string | null;
    labels: string[];
    agent: string | null;
  }) => void;
}) {
  const [title, setTitle] = useState(initial?.title ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [column, setColumn] = useState(columns[0] ?? "Backlog");
  const [priority, setPriority] = useState<Priority>(
    (initial?.priority as Priority) ?? "none"
  );
  const [labelsInput, setLabelsInput] = useState(
    (initial?.labels ?? []).join(", ")
  );
  const [agent, setAgent] = useState<string>(defaultAgent ?? "");

  const labels = parseLabels(labelsInput);

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className={MODAL_SIZES.MD}>
        <DialogHeader>
          <DialogTitle>{initial ? "Edit card" : "New card"}</DialogTitle>
          <DialogDescription>
            {initial ? "Update the card details below." : "Create a new card for your kanban board."}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="card-title">Title</Label>
            <Input
              id="card-title"
              placeholder="Enter card title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>

          <div className="space-y-2">
            <Label>Description</Label>
            <MarkdownPreviewToggle value={description} onChange={setDescription} />
          </div>

          <div className="grid grid-cols-2 gap-4">
            {!initial && (
              <div className="space-y-2">
                <Label>Column</Label>
                <Select value={column} onValueChange={setColumn}>
                  <SelectTrigger>
                    <SelectValue placeholder="Select column" />
                  </SelectTrigger>
                  <SelectContent>
                    {columns.map((c) => (
                      <SelectItem key={c} value={c}>
                        {c}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}

            <div className="space-y-2">
              <Label>Priority</Label>
              <Select value={priority} onValueChange={(v) => setPriority(v as Priority)}>
                <SelectTrigger>
                  <SelectValue placeholder="Select priority" />
                </SelectTrigger>
                <SelectContent>
                  {PRIORITIES.map((p) => (
                    <SelectItem key={p} value={p}>
                      {p}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="card-agent">Agent</Label>
            <Input
              id="card-agent"
              placeholder="Agent name (optional)"
              value={agent}
              onChange={(e) => setAgent(e.target.value)}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="card-labels">Labels</Label>
            <Input
              id="card-labels"
              placeholder="comma, separated, labels"
              value={labelsInput}
              onChange={(e) => setLabelsInput(e.target.value)}
            />
            {labels.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {labels.map((l) => (
                  <Badge key={l} variant="outline" className="text-[10px] font-normal">
                    {l}
                  </Badge>
                ))}
              </div>
            )}
          </div>
        </div>

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
                column,
                priority: priority === "none" ? null : priority,
                labels,
                agent: agent.trim() || null,
              })
            }
          >
            {initial ? "Update" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
