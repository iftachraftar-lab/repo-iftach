const CACHE_NAME = "nutriagent-v5";

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      )
    ).then(() => self.clients.claim())
  );
});

// Network-first strategy so deployments are always reflected immediately
self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;

  event.respondWith(
    fetch(event.request)
      .then((networkResponse) => {
        if (
          networkResponse &&
          networkResponse.status === 200 &&
          event.request.url.startsWith(self.location.origin) &&
          !event.request.url.includes("/api/")
        ) {
          const cloned = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, cloned));
        }
        return networkResponse;
      })
      .catch(() => caches.match(event.request))
  );
});

// Handle incoming Web Push Notifications on Mobile & Desktop
self.addEventListener("push", (event) => {
  let payload = {
    title: "NutriAgent AI 🔔",
    body: "עדכון חדש במעקב התזונה שלך!",
    url: "/?source=push",
    tag: "nutriagent-push",
  };

  if (event.data) {
    try {
      const parsed = event.data.json();
      payload = { ...payload, ...parsed };
    } catch (e) {
      payload.body = event.data.text() || payload.body;
    }
  }

  const options = {
    body: payload.body,
    icon: "/static/icon.svg",
    badge: "/static/icon.svg",
    dir: "rtl",
    lang: "he",
    tag: payload.tag || "nutriagent-push",
    renotify: true,
    vibrate: [200, 100, 200],
    data: {
      url: payload.url || "/?source=push",
    },
  };

  event.waitUntil(self.registration.showNotification(payload.title, options));
});

// Open or focus the PWA when the user taps a push notification
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = event.notification.data?.url || "/";

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url.includes(self.location.origin) && "focus" in client) {
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(targetUrl);
      }
    })
  );
});
