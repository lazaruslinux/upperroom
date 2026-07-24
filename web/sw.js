// A service worker that caches nothing.
//
// It exists only because Chrome on Android will not offer to install a site
// without one. Caching here would be actively harmful: every asset is
// cache-busted with ?v=N and served immutable, so a stale copy in a worker
// cache would outlive its version bump, and on a site behind a sign in a
// cached page can be shown to whoever picks the phone up next. So every
// request goes straight to the network, exactly as it would without this file.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (event) => event.respondWith(fetch(event.request)));
