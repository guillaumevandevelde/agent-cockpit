import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { MarkdownPreviewToggle } from "@/components/shared/MarkdownPreviewToggle";
import { MODAL_SIZES } from "@/lib/constants";

export function CardEditDialog({
  open,
  initial,
  onClose,
  onSubmit,
}: {
  open: boolean;
  initial?: { title: string; description: string };
  onClose: () => void;
  onSubmit: (data: { title: string; description: string }) => void;
}) {
  const [title, setTitle] = useState(initial?.title ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");

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
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={!title.trim()}
            onClick={() => onSubmit({ title, description })}
          >
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
