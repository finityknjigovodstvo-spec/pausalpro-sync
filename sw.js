// Finity Portal — Service Worker
// Sluša push notifikacije u pozadini, čak i kad je portal zatvoren.

self.addEventListener('install', (e) => {
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  self.clients.claim();
});

// ── PRIJEM PUSH PORUKE SA SERVERA ──
self.addEventListener('push', (event) => {
  let podaci = { naslov: 'Finity Portal', telo: 'Imate novo obaveštenje.', url: '/portal' };
  try {
    if (event.data) podaci = event.data.json();
  } catch (e) {}

  const opcije = {
    body: podaci.telo,
    icon: 'data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 100 100\'%3E%3Crect width=\'100\' height=\'100\' rx=\'22\' fill=\'%231B2B4B\'/%3E%3Ctext x=\'50\' y=\'68\' font-size=\'55\' font-family=\'Arial\' font-weight=\'900\' fill=\'%23C9A84C\' text-anchor=\'middle\'%3E%D0%A4%3C/text%3E%3C/svg%3E',
    badge: 'data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 100 100\'%3E%3Crect width=\'100\' height=\'100\' fill=\'%231B2B4B\'/%3E%3C/svg%3E',
    vibrate: [200, 100, 200],
    data: { url: podaci.url || '/portal' },
    tag: podaci.tag || 'finity-notif',
    renotify: true,
  };

  event.waitUntil(self.registration.showNotification(podaci.naslov, opcije));
});

// ── KLIK NA NOTIFIKACIJU — otvori portal ──
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = event.notification.data?.url || '/portal';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url.includes('/portal') && 'focus' in client) return client.focus();
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});
