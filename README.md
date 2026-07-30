# Prüfversand

Programme Python simple pour préparer et tracer l'envoi hebdomadaire des
Prüfprotokolle. Il n'y a pas d'interface Web dans le MVP.

## Flux prévu

1. importer un CSV fictif ou validé contenant les Baustellen, Aufträge et
   Empfänger ;
2. scanner régulièrement le dossier du NAS ;
3. contrôler le PDF, son nom, sa stabilité et son SHA-256 ;
4. mettre de côté tout fichier ambigu ;
5. regrouper les nouveaux PDF par Auftrag ;
6. envoyer une fois par semaine ;
7. conserver l'historique dans MySQL.

## Démarrage HOME

Python 3.11 ou plus récent suffit pour les premiers contrôles :

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m pruefversand --config config/home.example.toml healthcheck
PYTHONPATH=src python -m pruefversand --config config/home.example.toml validate-csv sample_data/baustellen_example.csv
PYTHONPATH=src python -m pruefversand --config config/home.example.toml scan --dry-run
```

Le profil HOME impose `dry_run = true` et `mail.provider = "mock"`. Aucun vrai
e-mail n'est envoyé.

Pour générer le PDF entièrement synthétique du faux NAS :

```bash
python scripts/create_sample_pdf.py
```

## MySQL

Le schéma initial se trouve dans `sql/migrations/001_initial_schema.sql`.
L'adaptateur Python utilise le paquet officiel `mysql-connector-python`,
déclaré comme dépendance optionnelle. L'import CSV valide tout le fichier avant
la transaction, prend un verrou MySQL et déduplique le fichier par SHA-256.

Compose publie MySQL uniquement sur `127.0.0.1:3307` par défaut. Le port peut
être changé avec `PRUEFVERSAND_MYSQL_PORT`, sans modifier le port interne 3306.
Après démarrage d'un MySQL HOME contrôlé et définition locale du secret :

```powershell
$env:PYTHONPATH = 'src'
python -m pruefversand --config config/home.example.toml import-csv sample_data/baustellen_example.csv
python -m pruefversand --config config/home.example.toml scan
```

Le premier scan persistant classe un nouveau PDF `DISCOVERED`. Un second scan
inchangé est requis pour `READY`. `scan --dry-run` ne se connecte jamais à
MySQL et annonce `DRY_RUN_COMPLETE_NO_PERSISTENCE` pour ne pas être confondu
avec un contrôle de persistance.

`healthcheck` contrôle le chemin NAS puis MySQL avec une requête en lecture et
un timeout de cinq secondes. Il nécessite la variable de secret indiquée dans
la configuration, sans jamais en afficher la valeur, et retourne `NOT_READY`
avec un code non nul si le NAS ou MySQL est indisponible.

Le test HOME lance uniquement l'image locale `mysql:8.4` dans un conteneur
éphémère avec une base synthétique, un secret aléatoire et un port dynamique :

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_mysql_integration.ps1
```

Le script ne télécharge rien, ne touche pas au service MySQL existant et
supprime son conteneur temporaire après le test.

## Démonstration visuelle HOME

La démonstration complète démarre un MySQL 8.4 éphémère sur 3307, importe le
CSV synthétique, effectue deux scans puis écrit un `.eml` local :

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/demo_home.ps1
```

La commande Python exécutée par ce lanceur est `demo-home`. Elle refuse tout
provider autre que `mock`, tout domaine autre que `example.invalid` et tout
profil HOME sans `dry_run=true`. Aucun serveur de messagerie n'est contacté.

Pour vérifier l'anti-doublon avec deux appels sur la même base MySQL sans
supprimer les anciens `.eml` :

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/demo_home.ps1 -ReplayTwice
```

Pour démontrer qu'un Auftrag inconnu et un nom ambigu passent en
`MANUAL_REVIEW` sans produire de Versand ni de nouveau `.eml` :

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/demo_manual_review_home.ps1
```

Ce lanceur utilise deux PDF synthétiques dans
`sample_data/nas_mock/demo_manual_review`, MySQL Docker sur 3307 et la commande
`demo-manual-review-home`. Il vérifie que le compteur `.eml` reste inchangé.

## Gestion HOME de MANUAL_REVIEW

Le lanceur suivant utilise uniquement le conteneur MySQL 8.4 HOME conservé sur
3307. Il ne crée aucune base, n'importe aucun CSV et n'effectue aucun scan.
Les problèmes d'identification PDF et les incidents Versand sont consultés
séparément :

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/manual_review_home.ps1 -Action List
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/manual_review_home.ps1 -Action VersandList

powershell -NoProfile -ExecutionPolicy Bypass -File scripts/manual_review_home.ps1 -Action Assign -Filename '999999_DEMO_Unbekannt.pdf' -Auftragsnummer '00125083' -Reason 'Association HOME synthetique'

powershell -NoProfile -ExecutionPolicy Bypass -File scripts/manual_review_home.ps1 -Action Ignore -Filename 'F0661.pdf' -Reason 'Document HOME synthetique ignore'
```

`List` affiche seulement les Prüfprotokolle bloqués par un problème
d'identification. `VersandList` affiche les Versand en `MANUAL_REVIEW`, leurs
PDF liés et le blocage du retry automatique. Ces consultations utilisent une
transaction MySQL `READ ONLY`, comparent l'état avant/après et vérifient que le
compteur `.eml` ne change pas. `Assign` et `Ignore` exigent toujours la saisie
exacte `OUI`.

Pour résoudre un timeout Versand, choisir une seule des trois décisions. Chaque
commande demande `OUI`, enregistre la date et la raison dans MySQL et reste en
HOME avec le fournisseur mock :

```powershell
# 1. Le fournisseur confirme qu'il avait accepté le premier essai : aucun retry.
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/manual_review_home.ps1 -Action VersandAccept -VersandId 3 -Reason 'Fournisseur mock confirme ACCEPTED'

# 2. Le fournisseur confirme qu'il ne l'avait pas accepté : un retry mock unique.
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/manual_review_home.ps1 -Action VersandRetry -VersandId 3 -Reason 'Fournisseur mock confirme non accepte'

# 3. Le résultat reste inconnu : journaliser et conserver MANUAL_REVIEW.
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/manual_review_home.ps1 -Action VersandKeep -VersandId 3 -Reason 'Resultat fournisseur toujours inconnu'
```

`VersandRetry` réutilise le Versand existant, revalide le PDF et son SHA-256,
et crée au maximum un `.eml` mock local. Aucun de ces chemins n'appelle
`manual-review assign` ou `ignore`.

Sur une base HOME déjà préparée, les commandes Python directes sont :

```powershell
$env:PYTHONPATH = 'src'
python -m pruefversand --config config/home.example.toml manual-review list
python -m pruefversand --config config/home.example.toml versand-review list
python -m pruefversand --config config/home.example.toml manual-review assign <ID> '00125083' --reason 'Association HOME synthetique'
python -m pruefversand --config config/home.example.toml manual-review ignore <ID> --reason 'Document HOME synthetique ignore'
```

## Versand mock après association manuelle

La commande suivante sélectionne dans MySQL les Prüfprotokolle `READY` non
encore liés à un Versand, revalide leur taille et leur SHA-256, puis crée les
`.eml` mock :

```powershell
$env:PYTHONPATH = 'src'
python -m pruefversand --config config/home.example.toml prepare-mock-versand
```

La même commande exécute le cycle hebdomadaire lorsqu'un instant simulé est
fourni. Le décalage UTC est obligatoire et l'horloge Windows n'est pas modifiée :

```powershell
python -m pruefversand --config config/home.example.toml prepare-mock-versand --at '2026-07-31T16:00:00+02:00'
```

Pour tester localement un timeout ambigu sans réseau ni véritable envoi :

```powershell
python -m pruefversand --config config/home.example.toml prepare-mock-versand --simulate-timeout
python -m pruefversand --config config/home.example.toml versand-review list
```

Le premier appel ne crée aucun `.eml`. Il place le Versand et ses
Prüfprotokolle en `MANUAL_REVIEW`, enregistre la raison et bloque toute nouvelle
tentative automatique. Le second appel sert uniquement à vérifier que les
compteurs restent inchangés.

Le point de crash juste après le retour du mock est également simulable, mais
uniquement sur une base HOME/TEST isolée et avec des PDF synthétiques :

```powershell
python -m pruefversand --config <config-test.toml> prepare-mock-versand --simulate-crash-after-provider
python -m pruefversand --config <config-test.toml> prepare-mock-versand
python -m pruefversand --config <config-test.toml> versand-review list
```

Le premier processus écrit un seul `.eml`, puis s'arrête avant de persister
`ACCEPTED`. Le cycle suivant ne rappelle pas le mock : il passe le Versand et
ses Prüfprotokolle en `MANUAL_REVIEW` et journalise le résultat ambigu.

Pour isoler deux PDF synthétiques dans le faux NAS, les deux observations
stables peuvent réutiliser la commande `scan` existante :

```powershell
python -m pruefversand --config config/home.example.toml scan --path sample_data/nas_mock/demo_weekly_grouped
python -m pruefversand --config config/home.example.toml scan --path sample_data/nas_mock/demo_weekly_grouped
```

Le cycle HOME complet — deux scans, association de `F0661.pdf`, premier
Versand mock et seconde exécution anti-doublon — est reproductible avec :

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/demo_manual_assignment_versand_home.ps1
```

Le lanceur utilise uniquement MySQL Docker sur 3307, des données synthétiques,
`dry_run=true`, le fournisseur mock et des Empfänger `example.invalid`.

## Paquet HOME et état Git

La racine livrée n'est pas un dépôt Git et aucun `git init` implicite n'est
effectué. Cette absence est volontairement documentée ; Git pourra être
initialisé après extraction sur un poste autorisé.

Le paquet HOME final est construit par liste blanche :

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_home_package.ps1
```

Le script crée `Pruefversand_HOME_2026-07-30.zip` et son fichier
`.zip.sha256`. Il refuse d'écraser un paquet existant, exclut les secrets,
PDF, `.eml`, logs, caches et données générées, puis contrôle le manifeste
SHA-256 directement depuis le ZIP.

## Documentation

- [Exigences](docs/REQUIREMENTS.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Modèle de données](docs/DATA_MODEL.md)
- [Plan d'exécution](docs/EXECUTION_PLAN.md)
- [État du projet](docs/PROJECT_STATUS.md)
- [Transfert HOME vers COMPANY](docs/HOME_TO_COMPANY_DEPLOYMENT.md)
