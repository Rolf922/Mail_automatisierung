# Runbook d'exploitation

Ce document décrit le fonctionnement cible. Les commandes dépendant de MySQL
seront activées au fil des jalons.

## Contrôles HOME disponibles

```bash
PYTHONPATH=src python -m pruefversand --config config/home.example.toml healthcheck
PYTHONPATH=src python -m pruefversand --config config/home.example.toml validate-csv sample_data/baustellen_example.csv
PYTHONPATH=src python -m pruefversand --config config/home.example.toml scan --dry-run
```

`healthcheck` est le contrôle opérationnel : il vérifie le NAS et une connexion
MySQL en lecture, avec un timeout de cinq secondes. `scan --dry-run` reste un
contrôle local du NAS sans accès MySQL ni persistance ; son statut
`DRY_RUN_COMPLETE_NO_PERSISTENCE` ne signifie donc pas que MySQL est disponible.

## Commandes cibles

```text
pruefversand healthcheck
pruefversand import-csv fichier.csv --dry-run
pruefversand import-csv fichier.csv
pruefversand scan
pruefversand run-due --dry-run
pruefversand run-due
pruefversand list-quarantine
pruefversand assign-protokoll
pruefversand mark-manually-sent
pruefversand retry-failed
```

## Règles d'incident

- NAS indisponible : ne changer aucun état d'envoi ; réessayer plus tard.
- MySQL indisponible : arrêter avant tout appel e-mail.
- PDF invalide : quarantaine, jamais suppression de la source.
- Auftrag ou Empfänger inconnu : `MANUAL_REVIEW`.
- Timeout e-mail ambigu : `MANUAL_REVIEW`, pas de retry automatique.
- Crash ou arrêt après le début de l'appel fournisseur : ne jamais relancer le
  lot. Le prochain cycle convertit le Versand `SENDING` en `MANUAL_REVIEW` et
  journalise l'incident. Un `.eml` mock retrouvé prouve seulement sa création
  locale ; il ne prouve ni acceptation réelle ni livraison.
- Pour un timeout Versand, consulter `VersandList`, puis choisir après preuve
  fournisseur : `VersandAccept`, `VersandRetry` ou `VersandKeep`. Ne jamais
  utiliser `assign` ou `ignore` pour cet incident et ne jamais relancer sans la
  confirmation interactive `OUI`.
- Échec certain : retry limité selon la politique configurée.
- Double exécution : la seconde quitte sans traiter.

## Sauvegarde

Une sauvegarde quotidienne de MySQL et un test périodique de restauration seront
définis avec l'IT. Une sauvegarde non restaurée en test n'est pas considérée
comme validée.
