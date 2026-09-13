/* 朱兒的獨自升級（原名積分自律系統）的離線支援。
   策略跟工作台、記帳一樣：優先抓網路，抓不到才用快取。
   反過來（快取優先）會讓改完版還一直看到舊畫面。
   積分資料走 Firebase 與 localStorage，不經過這裡。 */

const CACHE = 'dora-points-v3';
const SHELL = ['./','./index.html','./manifest.webmanifest','./icon-192.png','./icon-512.png','./apple-touch-icon.png','./brand.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const req = e.request;
  if(req.method !== 'GET') return;
  const url = new URL(req.url);
  if(url.origin !== location.origin) return;      // Firebase、Google 登入、字型一律直接走網路
  e.respondWith(
    fetch(req).then(res => {
      const copy = res.clone();
      caches.open(CACHE).then(c => c.put(req, copy)).catch(()=>{});
      return res;
    }).catch(() => caches.match(req).then(r => r || caches.match('./index.html')))
  );
});
