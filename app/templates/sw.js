// Service worker - נדרש כדי שהאפליקציה תהיה ניתנת להתקנה.
// גרסה: {{ version }}
//
// אסטרטגיה: נכסים סטטיים מהמטמון, דפים מהרשת. האפליקציה מציגה
// מחירים, מלאי וזיהוי רכב חי - הגשת דף מקאש הייתה מציגה נתונים
// ישנים כאילו הם עדכניים, וזה גרוע יותר מהודעת ניתוק.

const VERSION = "{{ version }}";
const SHELL_CACHE = `makat-shell-${VERSION}`;

const SHELL = [
  "{{ url_for('static', filename='css/style.css') }}",
  "{{ url_for('static', filename='css/welcome.css') }}",
  "{{ url_for('static', filename='js/app.js') }}",
  "{{ url_for('static', filename='js/car3d.js') }}",
  "{{ url_for('static', filename='icons/icon-192.png') }}",
  "{{ url_for('pwa.offline') }}",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => key !== SHELL_CACHE).map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;

  // רק GET נשמר. POST הוא חיפוש או שמירה - חייב להגיע לשרת.
  if (request.method !== "GET" || new URL(request.url).origin !== self.location.origin) {
    return;
  }

  // נכסים סטטיים: מהמטמון קודם, הם לא משתנים בין גרסאות
  if (new URL(request.url).pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(request).then((hit) => hit || fetch(request).then((response) => {
        const copy = response.clone();
        caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
        return response;
      }))
    );
    return;
  }

  // דפים: מהרשת בלבד, עם מסך ניתוק כשאין חיבור
  event.respondWith(
    fetch(request).catch(() =>
      caches.match("{{ url_for('pwa.offline') }}").then(
        (hit) => hit || new Response("אין חיבור לרשת", {
          status: 503,
          headers: { "Content-Type": "text/plain; charset=utf-8" },
        })
      )
    )
  );
});
