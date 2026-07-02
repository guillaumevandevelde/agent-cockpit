import { apiClient } from "./api";

/** Per-category muting, mirrored on the backend PushSubscription row. */
export interface PushPreferences {
  mute_input: boolean;
  mute_completion: boolean;
  mute_error: boolean;
}

export const DEFAULT_PREFERENCES: PushPreferences = {
  mute_input: false,
  mute_completion: false,
  mute_error: false,
};

interface VapidKeyResponse {
  public_key: string | null;
  configured: boolean;
}

interface PushTestResponse {
  sent: number;
}

/** Real push needs a service worker, the Push API, and the Notifications API. */
export function isPushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

/** Decode a base64url VAPID key into the byte buffer `subscribe()` expects. */
function urlBase64ToUint8Array(base64String: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const output = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) output[i] = raw.charCodeAt(i);
  return output;
}

export async function registerServiceWorker(): Promise<ServiceWorkerRegistration> {
  return navigator.serviceWorker.register("/sw.js");
}

export async function getVapidPublicKey(): Promise<string | null> {
  const res = await apiClient<VapidKeyResponse>("push/vapid-public-key");
  return res.configured ? res.public_key : null;
}

/** Subscribe this browser to push and persist it (with prefs) on the backend. */
export async function subscribeToPush(
  prefs: PushPreferences,
): Promise<PushSubscription> {
  const registration = await navigator.serviceWorker.ready;
  const publicKey = await getVapidPublicKey();
  if (!publicKey) throw new Error("Push is niet geconfigureerd op de server.");

  let subscription = await registration.pushManager.getSubscription();
  if (!subscription) {
    subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(publicKey),
    });
  }

  const json = subscription.toJSON();
  await apiClient("push/subscribe", {
    method: "POST",
    body: JSON.stringify({
      endpoint: subscription.endpoint,
      keys: json.keys,
      user_agent: navigator.userAgent,
      ...prefs,
    }),
  });
  return subscription;
}

export async function unsubscribeFromPush(): Promise<void> {
  const registration = await navigator.serviceWorker.ready;
  const subscription = await registration.pushManager.getSubscription();
  if (!subscription) return;
  await apiClient("push/unsubscribe", {
    method: "POST",
    body: JSON.stringify({ endpoint: subscription.endpoint }),
  }).catch(() => {});
  await subscription.unsubscribe();
}

export async function updatePushPreferences(prefs: PushPreferences): Promise<void> {
  const registration = await navigator.serviceWorker.ready;
  const subscription = await registration.pushManager.getSubscription();
  if (!subscription) return;
  await apiClient("push/preferences", {
    method: "PATCH",
    body: JSON.stringify({ endpoint: subscription.endpoint, ...prefs }),
  });
}

export async function sendTestPush(): Promise<number> {
  const res = await apiClient<PushTestResponse>("push/test", { method: "POST" });
  return res.sent;
}

export async function getExistingSubscription(): Promise<PushSubscription | null> {
  if (!isPushSupported()) return null;
  const registration = await navigator.serviceWorker.ready;
  return registration.pushManager.getSubscription();
}
