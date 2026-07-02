import { useCallback, useEffect, useState } from "react";
import {
  DEFAULT_PREFERENCES,
  getExistingSubscription,
  isPushSupported,
  registerServiceWorker,
  sendTestPush,
  subscribeToPush,
  unsubscribeFromPush,
  updatePushPreferences,
  type PushPreferences,
} from "@/lib/push";

const PREFS_KEY = "push-preferences";

function readPrefs(): PushPreferences {
  if (typeof window === "undefined") return DEFAULT_PREFERENCES;
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (raw) return { ...DEFAULT_PREFERENCES, ...JSON.parse(raw) };
  } catch {
    /* ignore corrupt prefs */
  }
  return DEFAULT_PREFERENCES;
}

/**
 * Drives the real Web Push subscription for this browser: registers the service
 * worker, subscribes/unsubscribes, and keeps per-category muting in sync with
 * both localStorage and the backend.
 */
export function usePushSubscription() {
  const supported = isPushSupported();
  const [permission, setPermission] = useState<NotificationPermission>(
    supported ? Notification.permission : "denied",
  );
  const [subscribed, setSubscribed] = useState(false);
  const [preferences, setPreferences] = useState<PushPreferences>(readPrefs);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Register the SW and reflect any existing subscription on mount.
  useEffect(() => {
    if (!supported) return;
    let cancelled = false;
    (async () => {
      try {
        await registerServiceWorker();
        const existing = await getExistingSubscription();
        if (!cancelled) setSubscribed(Boolean(existing));
      } catch {
        /* SW registration failures leave the feature disabled */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [supported]);

  useEffect(() => {
    try {
      localStorage.setItem(PREFS_KEY, JSON.stringify(preferences));
    } catch {
      /* ignore */
    }
  }, [preferences]);

  const enable = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await Notification.requestPermission();
      setPermission(result);
      if (result !== "granted") {
        setError("Notificaties zijn geweigerd.");
        return;
      }
      await subscribeToPush(preferences);
      setSubscribed(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Inschrijven mislukt.");
    } finally {
      setBusy(false);
    }
  }, [preferences]);

  const disable = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      await unsubscribeFromPush();
      setSubscribed(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Uitschrijven mislukt.");
    } finally {
      setBusy(false);
    }
  }, []);

  const setPreference = useCallback(
    async (key: keyof PushPreferences, value: boolean) => {
      const next = { ...preferences, [key]: value };
      setPreferences(next);
      if (subscribed) {
        await updatePushPreferences(next).catch(() => {});
      }
    },
    [preferences, subscribed],
  );

  const test = useCallback(async () => {
    setError(null);
    try {
      return await sendTestPush();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Test mislukt.");
      return 0;
    }
  }, []);

  return {
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
  };
}
