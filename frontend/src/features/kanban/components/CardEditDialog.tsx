import { useState } from "react";
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
  onClose: () => void;
  onSubmit: (data: {
    title: string;
    description: string;
    priority: string | null;
    labels: string[];
  }) => void;
}) {
  const [title, setTitle] = useState(initial?.title ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [priority, setPriority] = useState<Priority>(
    (initial?.priority as Priority) ?? "none"
  );
  const [labelsInput, setLabelsInput] = useState(
    (initial?.labels ?? []).join(", ")
  );

  const labels = parseLabels(labelsInput);

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
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-muted-foreground w-16">
            Priority
          </span>
          <Select value={priority} onValueChange={(v) => setPriority(v as Priority)}>
            <SelectTrigger className="h-8 w-[160px]">
              <SelectValue placeholder="Priority" />
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
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-muted-foreground w-16">
              Labels
            </span>
            <Input
              placeholder="comma, separated, labels"
              value={labelsInput}
              onChange={(e) => setLabelsInput(e.target.value)}
            />
          </div>
          {labels.length > 0 && (
            <div className="flex flex-wrap gap-1 pl-[4.5rem]">
              {labels.map((l) => (
                <Badge key={l} variant="outline" className="text-[10px] font-normal">
                  {l}
                </Badge>
              ))}
            </div>
          )}
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
                priority: priority === "none" ? null : priority,
                labels,
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
