const CACHE_NAME = "nutriagent-v26";

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

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});

// Network-first strategy so deployments are always reflected immediately
self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;

  event.respondWith(
    fetch(event.request, { cache: "no-cache" })
      .then((networkResponse) => {
        if (
          networkResponse &&
          networkResponse.status === 200 &&
          event.request.url.startsWith(self.location.origin) &&
          !event.request.url.includes("/api/") &&
          !event.request.url.endsWith("/sw.js")
        ) {
          const cloned = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, cloned));
        }
        return networkResponse;
      })
      .catch(() => caches.match(event.request))
  );
});

// Handle incoming Web Push Notifications on Mobile (Android / iOS) & Desktop
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
    icon: "/static/icon-192.png",
    badge: "/static/icon-192.png",
    dir: "rtl",
    lang: "he",
    tag: payload.tag || "nutriagent-push",
    renotify: true,
    requireInteraction: false,
    vibrate: [200, 100, 200],
    data: {
      url: payload.url || "/?source=push",
    },
  };

  event.waitUntil(
    self.registration.showNotification(payload.title, options).catch(() =>
      // Fallback for iOS / strict mobile browsers that reject extra notification options
      self.registration.showNotification(payload.title, {
        body: payload.body,
        icon: "/static/icon-192.png",
        data: { url: payload.url || "/?source=push" },
      })
    )
  );
});

// Open or focus the PWA when the user taps a push notification
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  let rawUrl = event.notification.data?.url || "/";
  let targetUrl = "/";
  try {
    const parsed = new URL(rawUrl, self.location.origin);
    if (parsed.origin === self.location.origin) {
      targetUrl = parsed.pathname + parsed.search + parsed.hash;
    }
  } catch (e) {
    targetUrl = "/";
  }

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
