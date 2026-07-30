# Règles durables du projet Prüfversand

## But

Construire un programme Python simple, sans interface Web, qui importe les
Baustellen depuis un CSV, détecte les nouveaux Prüfprotokolle PDF sur un NAS,
les regroupe par Auftrag et prépare un envoi hebdomadaire traçable.

## Langue

- Communiquer principalement en français simple.
- Conserver les termes métier allemands : Auftrag, Auftragsnummer, Baustelle,
  Prüfprotokoll, Empfänger et Versand.
- Expliquer les décisions importantes et ne jamais prétendre qu'une
  intégration réelle a été testée lorsqu'elle a seulement été simulée.

## Environnement HOME

- Utiliser uniquement des données synthétiques ou anonymisées.
- Simuler le NAS avec `sample_data/nas_mock`.
- Utiliser uniquement le fournisseur e-mail `mock`.
- Maintenir `dry_run = true`.
- Ne jamais copier dans Git les fichiers présents dans `upload/`, les secrets,
  les `.env` réels, les logs, les e-mails générés ou les PDF clients.
- MySQL est la base cible. Si MySQL ou Docker n'est pas disponible, développer
  et tester la logique pure, les migrations et les adaptateurs sans inventer
  un test d'intégration MySQL réussi.

## Environnement COMPANY

Avant toute modification :

1. effectuer une inspection en lecture seule ;
2. documenter Windows, Python, PowerShell, MySQL, le NAS, la messagerie, les
   droits, le proxy, le pare-feu et les sauvegardes ;
3. obtenir l'accord explicite de l'utilisateur et, si nécessaire, de l'IT.

Ne jamais installer un paquet, créer une tâche planifiée, utiliser un secret,
modifier des données réelles ou envoyer un vrai e-mail sans cette autorisation.

## Invariants métier et sécurité

- `auftragsnummer` reste une chaîne pour conserver les zéros initiaux.
- Un PDF ambigu, inconnu, invalide ou identifié uniquement par OCR ne part pas.
- Le même SHA-256 ne peut pas être envoyé automatiquement deux fois.
- Un fichier doit être stable pendant deux scans avant de devenir `READY`.
- Un e-mail contient uniquement les nouveaux PDF d'un même Auftrag.
- L'envoi automatique est hebdomadaire ; le scan peut être plus fréquent.
- Un envoi urgent manuel doit être enregistré pour exclure les PDF du lot.
- Un timeout e-mail ambigu devient `MANUAL_REVIEW`, sans renvoi aveugle.
- `ACCEPTED` signifie accepté par le fournisseur, pas forcément livré.
- Aucune suppression automatique n'est déclenchée par l'absence d'une ligne
  dans un nouveau CSV.

## Architecture

- Python 3.11 ou plus récent.
- MySQL 8.4/InnoDB en cible.
- Configuration TOML séparée du code.
- CLI, sans interface Web dans le MVP.
- Scanner périodique du dossier NAS via un chemin Windows/UNC.
- Adaptateurs e-mail `mock`, puis Graph ou SMTP seulement après décision IT.
- Tests de logique avec la bibliothèque standard ; tests MySQL séparés.

## Commandes de qualité

Depuis la racine du projet :

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m pruefversand --config config/home.example.toml healthcheck
PYTHONPATH=src python -m pruefversand --config config/home.example.toml validate-csv sample_data/baustellen_example.csv
PYTHONPATH=src python -m pruefversand --config config/home.example.toml scan --dry-run
```

## Documentation à maintenir

Après chaque jalon important, mettre à jour au minimum :

- `docs/EXECUTION_PLAN.md`
- `docs/DECISIONS.md`
- `docs/PROJECT_STATUS.md`

Toute question liée à l'entreprise doit rester visible dans
`docs/REQUIREMENTS.md` ou `docs/COMPANY_DISCOVERY_CHECKLIST.md`.

