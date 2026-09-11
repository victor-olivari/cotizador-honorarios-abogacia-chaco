/* Service Worker - Honorarios Minimos Ley 4228-C Chaco */
const CACHE = 'honorarios-chaco-v2026-09-calculadora-b';
const STATIC = [
  './',
  './index.html',
  './instalar.html',
  './manifest.json',
  './uma-data.json',
  './favicon.ico',
  './icon-16.png',
  './icon-32.png',
  './icon-48.png',
  './icon-72.png',
  './icon-96.png',
  './icon-128.png',
  './icon-144.png',
  './icon-152.png',
  './icon-192.png',
  './icon-384.png',
  './icon-512.png',
  './icon-maskable-192.png',
  './icon-maskable-512.png',
  './apple-touch-icon.png',
  './browserconfig.xml'
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => {
      const old = keys.filter(k => k !== CACHE);
      return Promise.all(old.map(k => caches.delete(k)))
        .then(() => self.clients.claim())
        .then(() => {
          /* Si había versión anterior, recargar todas las ventanas abiertas.
             client.navigate() funciona incluso en páginas sin listener propio. */
          if (old.length === 0) return;
          return self.clients.matchAll({ type: 'window', includeUncontrolled: true })
            .then(clients => clients.forEach(c => c.navigate(c.url)));
        });
    })
  );
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;

  const url = e.request.url;

  /* UMA data: red-first (datos frescos), cache como respaldo offline */
  if (url.includes('uma-data.json') || url.includes('raw.githubusercontent.com')) {
    e.respondWith(
      fetch(e.request, { cache: 'no-cache' })
        .then(r => {
          const copy = r.clone();
          caches.open(CACHE).then(c => c.put(e.request, copy));
          return r;
        })
        .catch(() => caches.match(e.request))
    );
    return;
  }

  if (e.request.mode === 'navigate') {
    e.respondWith(
      fetch(e.request)
        .then(r => {
          const copy = r.clone();
          caches.open(CACHE).then(c => c.put(e.request, copy));
          return r;
        })
        .catch(() => caches.match(e.request).then(r => r || caches.match('./index.html')))
    );
    return;
  }

  /* Todo lo demás: cache-first (funciona sin conexión) */
  e.respondWith(
    caches.match(e.request).then(
      r => r || fetch(e.request).then(r2 => {
        const copy = r2.clone();
        caches.open(CACHE).then(c => c.put(e.request, copy));
        return r2;
      })
    )
  );
});
