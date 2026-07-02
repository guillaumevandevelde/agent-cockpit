import { useState } from "react";
import { Smartphone } from "lucide-react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { usePushSubscription } from "@/hooks/usePushSubscription";
import type { PushPreferences } from "@/lib/push";

const CATEGORIES: { key: keyof PushPreferences; label: string; hint: string }[] = [
  { key: "mute_input", label: "Input gevraagd", hint: "Sessie wacht op je of stelt een vraag" },
  { key: "mute_completion", label: "Klaar", hint: "Een sessie is beëindigd" },
  { key: "mute_error", label: "Fouten", hint: "Een commando faalde" },
];

export function PushSettings() {
  const [open, setOpen] = useState(false);
  const {
    supported,
    permission,
    subscribed,
    preferences,
    busy,
    error,
    enable,
    disable,
    setPreference,
    test,
  } = usePushSubscription();

  const blocked = permission === "denied";

  const onToggleMaster = async (checked: boolean) => {
    if (checked) await enable();
    else await disable();
  };

  const onTest = async () => {
    const sent = await test();
    if (sent > 0) toast.success(`Test verstuurd naar ${sent} apparaat/apparaten`);
    else toast.info("Geen ingeschreven apparaten om te testen");
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          title="Push-notificaties instellen"
          aria-label="Push-notificaties instellen"
        >
          <Smartphone className={subscribed ? "h-5 w-5 text-primary" : "h-5 w-5"} />
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Push-notificaties</DialogTitle>
          <DialogDescription>
            Krijg een echte melding op je telefoon of desktop wanneer een sessie je
            aandacht nodig heeft — ook als dit tabblad dicht is.
          </DialogDescription>
        </DialogHeader>

        {!supported ? (
          <p className="text-sm text-muted-foreground">
            Deze browser ondersteunt geen Web Push. Op iOS moet je de app eerst via
            "Zet op beginscherm" installeren (en de server moet HTTPS gebruiken).
          </p>
        ) : (
          <div className="space-y-5">
            <div className="flex items-center justify-between">
              <div>
                <Label htmlFor="push-master">Push inschakelen</Label>
                <p className="text-xs text-muted-foreground">Dit apparaat</p>
              </div>
              <Switch
                id="push-master"
                checked={subscribed}
                disabled={busy || blocked}
                onCheckedChange={onToggleMaster}
              />
            </div>

            {blocked && (
              <p className="text-xs text-destructive">
                Notificaties zijn geblokkeerd — sta ze toe in je browserinstellingen.
              </p>
            )}

            <div className="space-y-3">
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                Categorieën
              </p>
              {CATEGORIES.map(({ key, label, hint }) => (
                <div key={key} className="flex items-center justify-between">
                  <div>
                    <Label htmlFor={`push-${key}`}>{label}</Label>
                    <p className="text-xs text-muted-foreground">{hint}</p>
                  </div>
                  <Switch
                    id={`push-${key}`}
                    checked={!preferences[key]}
                    disabled={!subscribed || busy}
                    onCheckedChange={(checked) => setPreference(key, !checked)}
                  />
                </div>
              ))}
            </div>

            {error && <p className="text-xs text-destructive">{error}</p>}

            <Button variant="outline" className="w-full" disabled={busy} onClick={onTest}>
              Stuur test
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
