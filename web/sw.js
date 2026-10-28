'use strict';
// Service worker for Web Push only: shows the sudo approval request and opens the page on tap.
// Nothing is cached; the page always talks to the server directly.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_) { /* a non-JSON push still shows a generic notice */ }
  event.waitUntil(self.registration.showNotification(data.title || 'sudo approval request', {
    body: data.body || 'A command on the Mac is waiting for approval',
    tag: data.id || 'approval',
    data: { url: data.url || '/' },
    requireInteraction: true,
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = new URL(event.notification.data && event.notification.data.url || '/', self.location.origin).href;
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windows) => {
    for (const client of windows) {
      if ('focus' in client) { client.navigate(url); return client.focus(); }
    }
    return self.clients.openWindow(url);
  }));
});
