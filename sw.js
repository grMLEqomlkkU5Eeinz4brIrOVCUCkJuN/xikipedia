const SW_VERSION = '1.2.0';

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener("fetch", (event) => {
  const filename = new URL(event.request.url).pathname.split("/").at(-1);
  if (filename == "clearHtml")
    return event.respondWith((async () => {
      await caches.delete("html");
      return new Response("cleared", {
        status: 200,
        headers: { "Content-Type": "text/plain" },
      });
    })());
  if (filename == "swVer")
    return event.respondWith((()=>new Response(SW_VERSION, {
        status: 200,
        headers: { "Content-Type": "text/plain" },
      }))());
  if (!["", "index.html", "app.webmanifest", "favicon.ico", "favicon-48.png", "favicon-256.png", "smoldata.json"].includes(filename))
    return;
  event.respondWith((async () => {
    const request = event.request;
    const cachedResponse = await caches.match(request);
    if (cachedResponse)
      return cachedResponse;
    const isSmolData = filename == "smoldata.json";
    // todo: delete after verifying the new one downloaded
    if (isSmolData)
      await caches.delete("smoldata");
    const cache = await caches.open(isSmolData ? "smoldata" : "html");
    try {
      const networkResponse = await fetch(request);
      await cache.put(request, networkResponse.clone());
      return networkResponse;
    } catch (error) {
      return new Response("Network error happened", {
        status: 408,
        headers: { "Content-Type": "text/plain" },
      });
    }
  })());
});

