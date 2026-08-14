/* =====================================================================
   AK World — graphiques du tableau de bord (Chart.js servi en local)

   Expose window.AKGraphiques, utilisé par templates/dashboard.html.
   Ces graphiques ne sont affichés qu'aux profils Gérant et Consultant.

   Toutes les fonctions sont défensives : canvas absent, Chart.js non
   chargé ou série vide n'entraînent jamais d'erreur — au pire un message
   « pas encore assez de données » remplace le graphique.
   ===================================================================== */

window.AKGraphiques = (function () {
  'use strict';

  var BLEU = '#1c3d5a';
  var BLEU_CLAIR = '#4a7ba7';
  var VERT = '#2e8b57';
  var ROUGE = '#c0392b';

  var PALETTE_CHAUDE = ['#c0392b', '#c8912b', '#a0522d', '#8f4a5e',
                        '#b5651d', '#7d3c4a', '#d4844a', '#6b4423'];
  var PALETTE_FROIDE = ['#1c3d5a', '#2e8b57', '#4a7ba7', '#3d8b8b',
                        '#7d6b9e', '#5a8a9e', '#2f6f4f', '#6b7a8c'];

  /* ---------- Utilitaires ---------- */

  function formater(valeur) {
    if (valeur === null || valeur === undefined || isNaN(valeur)) return '0';
    return Math.round(valeur).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  }

  function disponible(id) {
    if (typeof Chart === 'undefined') {
      console.warn('Chart.js non chargé : graphiques désactivés.');
      return null;
    }
    var element = document.getElementById(id);
    return element ? element.getContext('2d') : null;
  }

  function serieVide(tableau) {
    if (!tableau || !tableau.length) return true;
    return tableau.every(function (v) { return !v; });
  }

  function afficherVide(id, message) {
    var element = document.getElementById(id);
    if (!element || !element.parentNode) return;
    element.parentNode.innerHTML = '<div class="graphique-vide">' +
      (message || 'Pas encore assez de données pour ce graphique.') + '</div>';
  }

  function axeMontant() {
    return {
      beginAtZero: true,
      grid: { color: 'rgba(0,0,0,.06)' },
      ticks: {
        font: { size: 11 },
        callback: function (valeur) { return formater(valeur); }
      }
    };
  }

  function infobulle(contexte) {
    var valeur = contexte.parsed.y !== undefined ? contexte.parsed.y
               : (contexte.parsed.x !== undefined ? contexte.parsed.x
               : contexte.parsed);
    var prefixe = contexte.dataset.label ? contexte.dataset.label + ' : ' : '';
    return ' ' + prefixe + formater(valeur) + ' FCFA';
  }

  function infobullePart(total) {
    return function (contexte) {
      var part = total ? (contexte.parsed / total * 100).toFixed(1) : '0.0';
      return ' ' + contexte.label + ' : ' + formater(contexte.parsed) +
             ' FCFA (' + part + ' %)';
    };
  }

  function somme(tableau) {
    return (tableau || []).reduce(function (a, b) { return a + (b || 0); }, 0);
  }

  /* ---------- 1. Évolution mensuelle ----------
     Ventes et achats en barres, résultat du mois en courbe. */

  function evolution(id, donnees) {
    var ctx = disponible(id);
    if (!ctx) return;
    if (serieVide(donnees.ventes) && serieVide(donnees.achats)) {
      return afficherVide(id);
    }

    new Chart(ctx, {
      type: 'bar',
      data: {
        labels: donnees.mois,
        datasets: [
          { label: 'Ventes', data: donnees.ventes, backgroundColor: VERT,
            borderRadius: 3, order: 2 },
          { label: 'Achats', data: donnees.achats, backgroundColor: ROUGE,
            borderRadius: 3, order: 2 },
          { label: 'Résultat du mois', data: donnees.resultats, type: 'line',
            borderColor: BLEU, backgroundColor: BLEU, borderWidth: 2.5,
            pointRadius: 3, pointHoverRadius: 5, tension: 0.3, order: 1 }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { position: 'bottom',
                    labels: { boxWidth: 12, font: { size: 12 }, padding: 14 } },
          tooltip: { callbacks: { label: infobulle } }
        },
        scales: {
          x: { grid: { display: false }, ticks: { font: { size: 11 } } },
          y: axeMontant()
        }
      }
    });
  }

  /* ---------- 1 bis. Ventes / dépenses (version simplifiée) ----------
     Deux séries seulement, sans courbe de résultat : lecture plus directe. */

  function ventesDepenses(id, donnees) {
    var ctx = disponible(id);
    if (!ctx) return;
    if (serieVide(donnees.ventes) && serieVide(donnees.achats)) {
      return afficherVide(id);
    }

    new Chart(ctx, {
      type: 'bar',
      data: {
        labels: donnees.mois,
        datasets: [
          { label: 'Ventes', data: donnees.ventes, backgroundColor: VERT,
            borderRadius: 3 },
          { label: 'Dépenses', data: donnees.achats, backgroundColor: ROUGE,
            borderRadius: 3 }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { position: 'bottom',
                    labels: { boxWidth: 12, font: { size: 12 }, padding: 14 } },
          tooltip: { callbacks: { label: infobulle } }
        },
        scales: {
          x: { grid: { display: false }, ticks: { font: { size: 11 } } },
          y: axeMontant()
        }
      }
    });
  }

  /* ---------- 2. Trésorerie cumulée ---------- */

  function cumul(id, donnees) {
    var ctx = disponible(id);
    if (!ctx) return;
    if (serieVide(donnees.cumul)) return afficherVide(id);

    var degrade = ctx.createLinearGradient(0, 0, 0, 260);
    degrade.addColorStop(0, 'rgba(28,61,90,.22)');
    degrade.addColorStop(1, 'rgba(28,61,90,.01)');

    new Chart(ctx, {
      type: 'line',
      data: {
        labels: donnees.mois,
        datasets: [{
          label: 'Trésorerie cumulée', data: donnees.cumul,
          borderColor: BLEU, backgroundColor: degrade,
          borderWidth: 2.5, fill: true, tension: 0.3,
          pointRadius: 3, pointHoverRadius: 5
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: infobulle } }
        },
        scales: {
          x: { grid: { display: false }, ticks: { font: { size: 11 } } },
          y: axeMontant()
        }
      }
    });
  }

  /* ---------- 3. Anneau (charges, modes de paiement...) ---------- */

  function beignet(id, labels, valeurs, palette) {
    var ctx = disponible(id);
    if (!ctx) return;
    if (serieVide(valeurs)) return afficherVide(id);

    var couleurs = (palette === 'chaud') ? PALETTE_CHAUDE : PALETTE_FROIDE;
    var total = somme(valeurs);

    new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: labels,
        datasets: [{ data: valeurs, backgroundColor: couleurs,
                     borderWidth: 2, borderColor: '#fff' }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '56%',
        plugins: {
          legend: { position: 'right',
                    labels: { boxWidth: 11, font: { size: 11.5 }, padding: 9 } },
          tooltip: { callbacks: { label: infobullePart(total) } }
        }
      }
    });
  }

  /* ---------- 4. Barres horizontales (principaux clients) ---------- */

  function barresHorizontales(id, labels, valeurs) {
    var ctx = disponible(id);
    if (!ctx) return;
    if (serieVide(valeurs)) {
      return afficherVide(id, 'Aucune vente rattachée à un client.');
    }

    new Chart(ctx, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [{ label: "Chiffre d'affaires", data: valeurs,
                     backgroundColor: BLEU_CLAIR, borderRadius: 3 }]
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: infobulle } }
        },
        scales: {
          x: axeMontant(),
          y: { grid: { display: false }, ticks: { font: { size: 11.5 } } }
        }
      }
    });
  }

  /* ---------- 5. Barres verticales (usage général) ---------- */

  function barres(id, labels, valeurs, libelle) {
    var ctx = disponible(id);
    if (!ctx) return;
    if (serieVide(valeurs)) return afficherVide(id);

    var couleurs = valeurs.map(function (v) { return v >= 0 ? BLEU : ROUGE; });

    new Chart(ctx, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [{ label: libelle || 'Montant', data: valeurs,
                     backgroundColor: couleurs, borderRadius: 3 }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: infobulle } }
        },
        scales: {
          x: { grid: { display: false }, ticks: { font: { size: 11.5 } } },
          y: axeMontant()
        }
      }
    });
  }

  /* ---------- Réglages communs ---------- */

  if (typeof Chart !== 'undefined') {
    Chart.defaults.font.family = "'Segoe UI', system-ui, Arial, sans-serif";
    Chart.defaults.color = '#6b7a8c';
  }

  return {
    evolution: evolution,
    ventesDepenses: ventesDepenses,
    cumul: cumul,
    beignet: beignet,
    barresHorizontales: barresHorizontales,
    barres: barres,
    formater: formater
  };
})();
