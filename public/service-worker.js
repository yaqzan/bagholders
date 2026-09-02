/**
 * Bagholder Terminal — Service Worker
 *
 * Strategy:
 *  - /api/*          → network-only (never serve stale trading data)
 *  - /static/*       → cache-first (CRA hashed assets, safe to cache permanently)
 *  - navigate        → network-first, fall back to cached /index.html (SPA offline shell)
 *  - everything else → cache-first with network fallback
 */

const CACHE_NAME = 'bagholder-v1';

const PRECACHE_URLS = [
  '/',
  '/index.html',
  '/manifest.json',
  '/favicon.svg',
];

// ── Install ────────────────────────────────────────────────────────────────
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())
  );
});

// ── Activate ───────────────────────────────────────────────────────────────
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key !== CACHE_NAME)
            .map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

// ── Fetch ──────────────────────────────────────────────────────────────────
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip non-GET and cross-origin requests
  if (request.method !== 'GET' || url.origin !== self.location.origin) {
    return;
  }

  // API: always hit the network, never cache
  if (url.pathname.startsWith('/api/')) {
    return;
  }

  // Static assets (CRA content-hashed bundles): cache-first, then network + cache
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.open(CACHE_NAME).then((cache) =>
        cache.match(request).then(
          (cached) =>
            cached ||
            fetch(request).then((response) => {
              cache.put(request, response.clone());
              return response;
            })
        )
      )
    );
    return;
  }

  // Navigation (HTML): network-first so the app always gets fresh HTML,
  // fall back to the cached shell so it still loads offline.
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() =>
        caches.match('/index.html')
      )
    );
    return;
  }

  // Everything else (icons, fonts, etc.): cache-first
  event.respondWith(
    caches.open(CACHE_NAME).then((cache) =>
      cache.match(request).then(
        (cached) =>
          cached ||
          fetch(request).then((response) => {
            cache.put(request, response.clone());
            return response;
          })
      )
    )
  );
});
