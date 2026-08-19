// Service worker PROSIGES / AK World.
//
// Stratégie volontairement prudente pour une appli de gestion :
//   - Pages (HTML) : toujours réseau d'abord, jamais de données obsolètes
//     affichées comme si elles étaient à jour. Le cache ne sert que de
//     secours si la connexion est vraiment coupée.
//   - Fichiers statiques (logo, icônes, scripts) : cache d'abord, ils
//     changent rarement et ça accélère le chargement.
//
// Changez CACHE_VERSION à chaque modification de cette liste pour forcer
// le renouvellement du cache chez les utilisateurs.
const CACHE_VERSION = 'akworld-v1';
const RESSOURCES_ESSENTIELLES = [
  '/connexion',
  '/static/manifest.json',
  '/static/img/logo_ak_world.png',
  '/static/img/icon-192.png',
  '/static/img/icon-512.png',
  '/static/js/chart.min.js',
  '/static/js/graphiques.js',
];

self.addEventListener('install', (evenement) => {
  evenement.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => cache.addAll(RESSOURCES_ESSENTIELLES))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (evenement) => {
  evenement.waitUntil(
    caches.keys().then((noms) =>
      Promise.all(noms.filter((n) => n !== CACHE_VERSION).map((n) => caches.delete(n)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (evenement) => {
  const requete = evenement.request;
  if (requete.method !== 'GET') return;

  const estNavigation = requete.mode === 'navigate';

  if (estNavigation) {
    // Pages : réseau d'abord, jamais de contenu comptable périmé.
    evenement.respondWith(
      fetch(requete).catch(() =>
        caches.match('/connexion').then((reponse) =>
          reponse || new Response(
            '<h1>Hors ligne</h1><p>Impossible de contacter le serveur AK World. Vérifiez votre connexion.</p>',
            { headers: { 'Content-Type': 'text/html; charset=utf-8' } }
          )
        )
      )
    );
    return;
  }

  const estRessourceStatique = new URL(requete.url).pathname.startsWith('/static/');
  if (estRessourceStatique) {
    // Fichiers statiques : cache d'abord, réseau en secours.
    evenement.respondWith(
      caches.match(requete).then((reponse) => reponse || fetch(requete))
    );
  }
});
