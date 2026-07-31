// Service worker mínimo: solo lo necesario para que el navegador permita
// instalar la app en el móvil. No cachea datos (la plantilla cambia cada
// jornada, así que siempre queremos la versión en vivo del servidor).
self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  event.respondWith(fetch(event.request));
});
