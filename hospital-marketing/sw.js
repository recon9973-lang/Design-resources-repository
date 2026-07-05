/* 베놈 체크업 PWA 서비스워커 — 앱 셸(무료 도구) 오프라인 캐시.
   자가진단 계산기·허브는 자기완결(인라인)이라 오프라인 동작. 리포트/데이터/외부(API)는
   항상 네트워크(캐시 안 함) — 실측 데이터 신선도 보장. */
const CACHE = 'venom-checkup-v4';
const SHELL = [
  './', './index.html', './offline.html',
  './self-check/', './location/', './landing/',
  './manifest.webmanifest',
  './icons/icon-192.png', './icons/icon-512.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE)
      // 개별 실패가 전체 설치를 막지 않도록 각각 시도
      .then((c) => Promise.allSettled(SHELL.map((u) => c.add(u))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  // 외부 오리진(Supabase 등)·데이터·리포트는 캐시하지 않는다(신선도·실측 우선).
  if (url.origin !== location.origin
      || url.pathname.includes('/data/')
      || url.pathname.includes('/reports/')) {
    return;
  }
  // 앱 셸: 캐시 우선, 없으면 네트워크(성공 시 캐시 갱신). 실패 시 —
  //   페이지 이동 요청은 오프라인 안내로, 그 외는 조용히 실패.
  const isNav = req.mode === 'navigate';
  e.respondWith(
    caches.match(req).then((hit) =>
      hit || fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        return res;
      }).catch(() => (isNav ? caches.match('./offline.html') : Response.error()))
    )
  );
});
