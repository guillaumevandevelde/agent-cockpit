/* Claude Cockpit service worker — Web Push receiver.
 *
 * Shows an OS notification for pushes sent by the backend (attention events and
 * "send test"), and focuses/opens the right page when the notification is clicked.
 * The backend payload shape is { title, body, url, tag, category }.
 */

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: "Claude Cockpit", body: event.data ? event.data.text() : "" };
  }

  const title = data.title || "Claude Cockpit";
  const options = {
    body: data.body || "",
    tag: data.tag,
    renotify: Boolean(data.tag),
    icon: "/claude-cockpit-logo.svg",
    badge: "/claude-cockpit-logo.svg",
    data: { url: data.url || "/" },
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || "/";

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      // Focus an existing tab and navigate it, otherwise open a new one.
      for (const client of clientList) {
        if ("focus" in client) {
          client.focus();
          if ("navigate" in client) {
            client.navigate(targetUrl).catch(() => {});
          }
          return;
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(targetUrl);
      }
    })
  );
});
