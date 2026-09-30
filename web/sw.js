// The service worker: the go-live notification, and nothing cached.
//
// It shows the push the server sends when the stream goes live (push.js signs
// a device up), and a tap on it opens the room. It also lets Chrome on Android
// offer to install the site. Caching here would be actively harmful: every
// asset is cache-busted with ?v=N and served immutable, so a stale copy in a
// worker cache would outlive its version bump, and on a site behind a sign in
// a cached page can be shown to whoever picks the phone up next. So every
// request goes straight to the network, exactly as it would without this file.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (event) => event.respondWith(fetch(event.request)));

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = {}; }
  // tag "live": a second notice replaces one still unread instead of stacking.
  event.waitUntil(self.registration.showNotification(data.title || "Live now", {
    body: data.body || "",
    icon: "/assets/icons/icon-192.png?v=1",
    tag: "live",
  }));
});

// Always the room, whatever the notice said: the URL never rides in a push.
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil((async () => {
    const open = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of open) {
      const url = new URL(client.url);
      if (url.origin === self.location.origin && url.pathname === "/watch" && "focus" in client) {
        return client.focus();
      }
    }
    return self.clients.openWindow("/watch");
  })());
});
