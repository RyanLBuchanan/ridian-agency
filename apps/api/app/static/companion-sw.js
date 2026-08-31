/* Ridian Companion service worker (v6.9.7) — Web Push only.
   No fetch handler ON PURPOSE: the page's own honest-loading states
   (deadlines, retry, timeout messages) must never be masked by a cache.
   The PC sends {title, body, tag, tab}; tag doubles as the dedup key so
   the OS collapses any repeat, and tab is where a tap lands (item 5). */
"use strict";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_err) { /* non-JSON */ }
  e.waitUntil(self.registration.showNotification(d.title || "Ridian", {
    body: d.body || "",
    tag: d.tag || "",
    data: { tab: d.tab || "task" },
    icon: "/static/companion-icon-192.png",
    badge: "/static/companion-icon-maskable.png",
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const tab = (e.notification.data || {}).tab || "task";
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true })
    .then((wins) => {
      for (const w of wins) {
        if (w.url.includes("/companion")) {
          w.postMessage({ tab });
          return w.focus();
        }
      }
      return self.clients.openWindow("/companion#" + tab);
    }));
});
