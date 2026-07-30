# Plan de tests

## Tests automatisés HOME

- configuration HOME refuse un fournisseur réel ou `dry_run = false` ;
- CSV UTF-8/BOM et espaces ;
- conservation de `00125083` ;
- e-mail invalide et type destinataire invalide ;
- nom conforme et nom ambigu `F0661.pdf` ;
- PDF synthétique valide ;
- fichier `.pdf` sans signature PDF ;
- SHA-256 identique pour le même contenu ;
- premier scan `DISCOVERED`, second scan stable `READY` ;
- contenu modifié entre deux scans toujours `DISCOVERED` ;
- taille totale de pièces jointes contrôlée ;
- fournisseur mock refuse un domaine non autorisé.

## Tests MySQL HOME

- [x] migration sur base vide MySQL 8.4 ;
- [x] réexécution contrôlée des migrations ;
- [x] import du même CSV deux fois ;
- [x] rollback complet sur ligne invalide ;
- [x] absence d'un Empfänger dans un nouveau CSV sans suppression ni
  désactivation ;
- [x] unicité de l'Auftragsnummer et du SHA-256 ;
- [x] CLI réelle `import-csv` contre MySQL 8.4 ;
- [x] premier scan persistant `DISCOVERED`, second scan stable `READY` ;
- [x] PDF modifié remettant le compteur de stabilité à 1 ;
- [x] Auftrag inconnu classé `MANUAL_REVIEW` ;
- [x] `demo-home` avec deux scans, `.eml` mock et PDF joint ;
- [x] tous les Empfänger du `.eml` sous `example.invalid` ;
- [x] sujet `[TEST]` et signature PDF vérifiés par analyse MIME locale ;
- [x] deux appels `demo-home` sur la même base sans second `.eml` ;
- [x] rapprochement d'un ancien `.eml` mock dans une base vide ;
- [x] démonstration Auftrag inconnu et nom ambigu en `MANUAL_REVIEW`, sans
  Versand ni nouveau `.eml` ;
- [x] `manual-review list` avec identifiant, nom et raison ;
- [x] annulation de `assign` et `ignore` avant toute connexion MySQL ;
- [x] `assign` vers un Auftrag actif avec zéros initiaux conservés ;
- [x] Auftrag absent ou inactif refusé ;
- [x] décision refusée avant deux scans ou après changement du SHA-256 ;
- [x] `ignore` journalisé sans suppression du PDF ;
- [x] décision MANUAL_REVIEW sans création de Versand ni `.eml` ;
- [x] PDF associé manuellement sélectionné au prochain Versand mock ;
- [x] pièce jointe MIME identique au PDF synthétique et Empfänger
  `example.invalid` ;
- [x] seconde préparation excluant le PDF déjà lié, sans second `.eml` ;
- [x] deux scanners simultanés réels, un seul gagnant du verrou et une seule
  observation ;
- transitions d'état valides ;
- [x] limite cumulée : total exactement égal à la limite accepté et dépassement
  d'un octet bloqué en `MANUAL_REVIEW` avant appel fournisseur ;
- [x] lot hebdomadaire déterministe : deux PDF du même Auftrag dans un seul
  `.eml`, ancien PDF `ACCEPTED` exclu et seconde exécution sans doublon ;
- [x] timeout ambigu mock : Versand et PDF en `MANUAL_REVIEW`, raison et audit
  persistés, aucun `.eml`, incident visible et aucune seconde tentative ;
- [x] consultation séparée `versand-review list` en transaction `READ ONLY`,
  sans démo, import, scan, changement de statut ou nouvel `.eml` ;
- [x] confirmation fournisseur `ACCEPTED` journalisée sans retry ni `.eml` ;
- [x] résultat toujours inconnu journalisé avec statuts `MANUAL_REVIEW` stables ;
- [x] non-acceptation confirmée autorisant un seul retry mock manuel, second
  retry refusé et destinataires limités à `example.invalid` ;
- [x] retry manuel refusé si le SHA-256 a changé depuis le scan ;
- [x] interruption et redémarrage avant l'appel fournisseur, puis reprise
  unique ;
- [x] crash après le retour du fournisseur mock et avant `ACCEPTED` : un seul
  `.eml`, Versand et PDF d'abord `SENDING`, puis `MANUAL_REVIEW` après une
  nouvelle connexion, avec audit et sans retry ni doublon au passage suivant ;
- [x] envoi manuel empêchant le lot automatique.

## Tests COMPANY

- accès NAS en lecture avec le compte technique ;
- fichier en cours de copie ;
- NAS ou MySQL indisponible ;
- test Graph/SMTP vers une boîte interne ;
- timeout simulé ;
- limite de taille ;
- tâche Windows et blocage des exécutions simultanées ;
- rattrapage après PC éteint ;
- sauvegarde et restauration ;
- deux cycles pilotes sans doublon.

Chaque preuve doit contenir la date, l'environnement, la commande ou procédure,
le résultat et l'identité du validateur, sans secret ni donnée client inutile.
