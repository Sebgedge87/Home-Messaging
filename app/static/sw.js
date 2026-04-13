self.addEventListener('push', event => {
  let data = { title: 'Home Messaging', body: 'New message' };
  try { data = event.data.json(); } catch {}
  event.waitUntil(self.registration.showNotification(data.title, { body: data.body }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(clients.openWindow('/'));
});
