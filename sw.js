/* Family HQ service worker: instant home-screen launches and a proper offline screen.
   Never caches /api responses or the login page. Bump CACHE when the shell changes. */
const CACHE = 'family-hq-shell-v1';
const SHELL = ['/', '/manifest.json', '/icon-192.png', '/icon-512.png', '/offline.html'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL).catch(() => undefined)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname === '/login' || url.pathname === '/logout') return;

  if (request.mode === 'navigate') {
    // Network first so login state and fresh pages win; cached shell, then offline page, when there is no network.
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response.ok && url.pathname === '/') {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put('/', copy));
          }
          return response;
        })
        .catch(() => caches.match('/').then((cached) => cached || caches.match('/offline.html')))
    );
    return;
  }

  // Icons and manifest: cache first.
  event.respondWith(
    caches.match(request).then((cached) => cached || fetch(request).then((response) => {
      if (response.ok && SHELL.includes(url.pathname)) {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(request, copy));
      }
      return response;
    }))
  );
});
