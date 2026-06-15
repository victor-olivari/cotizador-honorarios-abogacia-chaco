/* Service Worker — Honorarios Mínimos Ley 4228-C Chaco */
const CACHE = 'honorarios-4228c-v2';
const STATIC = ['./', './index.html'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
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
