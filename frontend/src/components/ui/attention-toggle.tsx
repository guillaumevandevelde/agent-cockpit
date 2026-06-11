import { Bell, BellOff, BellRing } from "lucide-react";
import { Button } from "./button";
import { useAttention } from "@/contexts/AttentionContext";

export function AttentionToggle() {
  const { enabled, permission, toggle } = useAttention();

  const unsupported = permission === "unsupported";
  const blocked = enabled && permission === "denied";

  let icon = <BellOff className="h-5 w-5" />;
  let title = "Aandacht vragen aanzetten (desktop-notificaties)";

  if (unsupported) {
    title = "Desktop-notificaties niet ondersteund in deze browser";
  } else if (blocked) {
    icon = <BellRing className="h-5 w-5 text-destructive" />;
    title = "Notificaties geblokkeerd — sta ze toe in je browserinstellingen";
  } else if (enabled) {
    icon = <Bell className="h-5 w-5 text-primary" />;
    title = "Aandacht vragen uitzetten";
  }

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={toggle}
      disabled={unsupported}
      title={title}
      aria-pressed={enabled}
    >
      {icon}
      <span className="sr-only">Toggle attention notifications</span>
    </Button>
  );
}
