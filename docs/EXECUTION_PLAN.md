# Plan d'exécution

Version : 30 juillet 2026

## Phase HOME

### H1 — Cadrage durable

- [x] Lire le plan et le document de mission.
- [x] Écrire les exigences et décisions critiques.
- [x] Séparer HOME et COMPANY.
- [x] Fixer le format de nom et le cas `F0661.pdf`.

Critère de passage : aucune règle critique de sécurité ne reste implicite.

### H2 — Socle du projet

- [x] Créer `AGENTS.md`, `PLANS.md` et la documentation de reprise.
- [x] Créer le paquet Python et les configurations d'exemple.
- [x] Créer un CSV et un faux NAS synthétiques.
- [x] Documenter l'absence de dépôt Git et ne pas inventer d'historique.
- [ ] Initialiser Git après extraction sur un poste autorisé où `.git` est
  inscriptible.
- [x] Vérifier la dépendance MySQL dans l'environnement HOME contrôlé
  (`mysql-connector-python` 9.7.0 déjà présent, aucune installation effectuée).

Critère de passage : les contrôles sans MySQL s'exécutent sur une copie propre.

### H3 — Modèle MySQL et import CSV

- [x] Écrire la migration initiale MySQL.
- [x] Implémenter la lecture et la validation complète du CSV.
- [x] Implémenter la transaction d'upsert MySQL avec verrou nommé.
- [x] Tester migration, idempotence et rollback contre MySQL 8.4.
- [x] Exposer l'import transactionnel par la commande CLI `import-csv`.

Critère de passage : deux imports identiques ne changent pas le nombre
d'enregistrements et une erreur annule tout l'import.

Critère validé le 26 juillet 2026 sur un conteneur MySQL 8.4 éphémère,
avec le CSV synthétique et sans utiliser le service local existant sur 3306.

Preuve HOME complémentaire le 27 juillet 2026 : un premier CSV a créé les
Aufträge synthétiques `00991001` et `00991002`. Un second CSV a modifié
`00991001`, omis entièrement `00991002` et ajouté `00991003`. Après le second
import, `00991002` est resté actif avec ses deux Empfänger actifs et inchangés ;
zéro suppression et zéro désactivation ont été enregistrées. La réimportation
octet pour octet du second CSV a retourné `ALREADY_IMPORTED` grâce au SHA-256,
avec zéro insertion et zéro mise à jour. Aucun scan, Versand ou `.eml` n'a été
créé.

Preuve de désactivation explicite le 27 juillet 2026 : un CSV contenant
`00991003` et son Empfänger avec `aktiv=0` a conservé les IDs MySQL de l'Auftrag,
de la Baustelle et de l'Empfänger tout en passant les trois lignes à inactif.
Le résultat signale une mise à jour et une désactivation, sans suppression.
Un nouveau PDF synthétique stable a été classé `MANUAL_REVIEW` avec la raison
`Auftrag inconnu ou inactif`, puis est resté bloqué au troisième scan. Deux
cycles hebdomadaires ont créé zéro Versand et zéro `.eml`. Le réimport du même
CSV a retourné `ALREADY_IMPORTED` par SHA-256.

### H4 — Scanner NAS et quarantaine

- [x] Vérifier extension, signature, fin du PDF et SHA-256.
- [x] Extraire l'Auftragsnummer avec une règle configurable.
- [x] Comparer deux observations pour la stabilité.
- [x] Persister les observations et états dans MySQL.
- [x] Ajouter `manual-review list`, `assign` et `ignore` avec confirmation.

Critère de passage : aucun fichier instable, invalide ou ambigu ne devient
`READY`.

Persistance validée le 26 juillet 2026 : premier scan `DISCOVERED`, second
scan identique `READY`, modification ramenant le compteur à 1, et Auftrag
inconnu classé `MANUAL_REVIEW`.

Preuve HOME complémentaire le 26 juillet 2026 : la commande
`demo-manual-review-home` crée deux PDF synthétiques isolés. Le PDF portant
l'Auftrag inconnu `999999` et le fichier ambigu `F0661.pdf` passent tous deux
de `DISCOVERED` à `MANUAL_REVIEW` au second scan. Aucun Versand et aucun
nouveau `.eml` ne sont créés.

Gestion simple validée le 26 juillet 2026 : les décisions sont journalisées
dans MySQL avec date et raison. `assign` exige un Auftrag actif, deux scans et
un SHA-256 inchangé, conserve les zéros initiaux et ne crée aucun Versand.
`ignore` retire le PDF de la file d'attente sans supprimer le fichier. Une
annulation laisse le Prüfprotokoll inchangé en `MANUAL_REVIEW`.

### H5 — Lots et e-mail mock

- [x] Sélectionner les PDF `READY` dus.
- [x] Créer un lot déterministe par Auftrag et semaine.
- [x] Figer les Empfänger et les pièces jointes.
- [x] Écrire les messages `.eml` avec l'adaptateur mock.
- [x] Enregistrer un envoi urgent manuel.
- [x] Tester le regroupement hebdomadaire et l'anti-doublon.
- [x] Tester un timeout fournisseur ambigu sans retry automatique.
- [x] Résoudre manuellement un timeout : accepté, retry mock unique ou inconnu.
- [x] Tester l'interruption contrôlée et la reprise avant l'appel fournisseur.
- [x] Tester crash et reprise après l'appel fournisseur.

Critère de passage : le même lot rejoué ne crée ni second message ni second
Versand.

Preuve partielle HOME le 26 juillet 2026 : `demo-home` importe les données
synthétiques, exécute `DISCOVERED` puis `READY` et crée un `.eml` mock avec le
PDF en pièce jointe. La persistance transactionnelle des lots et la reprise
restent à réaliser.

Preuve d'anti-doublon le 26 juillet 2026 : deux appels `demo-home` sur la même
base MySQL ont conservé un seul `.eml` et un seul Versand. Le rapprochement
d'un `.eml` HOME existant vers une base vide est également testé. Les tests de
crash et de résultat e-mail ambigu restent à réaliser.

Preuve du cycle MANUAL_REVIEW le 27 juillet 2026 : `F0661.pdf` a été associé
manuellement à `00125083`, sélectionné depuis MySQL en `READY`, revalidé par
SHA-256 et joint à un nouveau `.eml` mock. La seconde exécution a trouvé zéro
document READY et un Prüfprotokoll déjà lié à `versand_protokoll`, sans créer
de second `.eml`.

Preuve du Versand hebdomadaire groupé le 27 juillet 2026 : deux nouveaux PDF
synthétiques de l'Auftrag `00125083` sont passés de `DISCOVERED` à `READY`.
L'instant `2026-07-31T16:00:00+02:00`, postérieur à l'heure planifiée, a été
fourni à la commande existante `prepare-mock-versand --at`, sans changer
l'horloge Windows. Un seul Versand, de période `2026-07-27`, et un seul `.eml`
contenant exactement les deux PDF ont été créés. `F0661.pdf`, déjà `ACCEPTED`,
n'a pas été repris. Une seconde exécution pour la même semaine a créé zéro
Versand et zéro `.eml`.

Preuve du timeout ambigu mock le 27 juillet 2026 : un nouveau PDF synthétique
de `00125083` est passé de `DISCOVERED` à `READY`, puis l'option locale
`prepare-mock-versand --simulate-timeout` a enregistré le Versand et le
Prüfprotokoll en `MANUAL_REVIEW`. La raison, la tentative et l'audit sont
persistés ; `accepted_at` reste vide et aucun `.eml` n'est créé. La seconde
exécution trouve zéro PDF `READY` et ne crée ni tentative, ni Versand, ni audit,
ni `.eml` supplémentaires. L'incident reste affiché séparément par
`versand-review list`, en transaction MySQL `READ ONLY`; `manual-review list`
reste réservé aux problèmes d'identification PDF.

Résolution HOME validée le 27 juillet 2026 : la migration `004` journalise la
décision, sa date, sa raison et l'acteur sans utiliser `assign` ou `ignore`.
`PROVIDER_ACCEPTED` passe le Versand et ses PDF à `ACCEPTED` sans nouvel
`.eml`. `PROVIDER_NOT_ACCEPTED_RETRY` revalide le NAS, la stabilité, la taille,
le SHA-256 et les Empfänger `example.invalid`, puis autorise exactement un
retry manuel mock. `KEEP_UNKNOWN` journalise la décision et conserve tous les
statuts en `MANUAL_REVIEW`. Les trois actions exigent la saisie exacte `OUI`.

Preuve du Versand urgent manuel le 27 juillet 2026 : après confirmation
explicite, le Prüfprotokoll synthétique ID `7` de l'Auftrag `00125083` a été
enregistré par `manual-versand record` avec son SHA-256, la date simulée,
l'opérateur `Utilisateur HOME`, la raison `Versand urgent synthétique` et un
Empfänger `example.invalid`. Le Versand manuel ID `4` et le PDF sont passés à
`MANUALLY_SENT` sans créer de `.eml`. Le prochain Versand hebdomadaire a joint
uniquement le second PDF, puis la relance de la même semaine a créé zéro
Versand et zéro `.eml`. La décision `KEEP_UNKNOWN` du Versand ID `3` est restée
inchangée.

Preuve de reprise avant fournisseur le 27 juillet 2026 : le PDF synthétique ID
`8` est passé de `DISCOVERED` à `READY`. L'interruption HOME a persisté le
Versand ID `6` en `PLANNED`, le PDF en `SENDING` et `versuch_anzahl=0`, sans
audit d'appel fournisseur ni nouvel `.eml`. La relance Python sur la même base
a repris uniquement cet état certain, appelé le mock une fois et produit un
seul `.eml` avec ce PDF. Deux relances, dont une après redémarrage du seul
conteneur MySQL sans suppression de volume, ont créé zéro Versand et zéro
`.eml`. Les états historiques `ACCEPTED`, `MANUALLY_SENT` et `KEEP_UNKNOWN`
sont restés inchangés.

Preuve de concurrence HOME le 28 juillet 2026 : deux vrais processus Python de
scan ont visé simultanément le même PDF synthétique de l'Auftrag `00991001`.
Un seul a obtenu le verrou et créé le Prüfprotokoll ID `10` en `DISCOVERED`
avec `stable_scan_count=1`; l'autre a quitté avec `SKIPPED_LOCK_BUSY`, sans
seconde observation. Le scan normal suivant l'a porté à `READY` avec le même
SHA-256. Deux vrais processus `prepare-mock-versand` ont ensuite utilisé
l'instant simulé `2026-08-28T16:00:00+02:00` : un seul a obtenu le verrou,
créé le Versand ID `7`, une liaison, un appel fournisseur mock et un `.eml` ;
l'autre a quitté avec `SKIPPED_LOCK_BUSY`. La relance normale a créé zéro
Versand, zéro appel mock et zéro `.eml`. Les deux verrous étaient libres après
chaque phase et le Versand ID `3` est resté `MANUAL_REVIEW` avec
`KEEP_UNKNOWN`.

Preuve d'indisponibilité HOME le 28 juillet 2026 : avec MySQL disponible, une
configuration temporaire pointant vers un NAS synthétique inexistant a produit
`healthcheck` code `1`, `NAS_UNAVAILABLE`, puis `scan --dry-run` code `2` avec
`Dossier NAS introuvable`, sans traceback. La configuration normale est ensuite
revenue à `READY`. Le conteneur du projet a été arrêté avec son volume conservé :
`healthcheck` a retourné en moins de trois secondes le code `1` et
`MYSQL_UNAVAILABLE`; le dry-run hors base a retourné le code `0` en indiquant
explicitement qu'il n'avait ni vérifié ni utilisé la persistance. Après
redémarrage du même conteneur, `mysqladmin` et le healthcheck applicatif ont
confirmé MySQL 8.4.10 disponible. Les empreintes de toutes les tables métier et
des six `.eml` sont restées identiques, les trois verrous étaient libres et la
relance hebdomadaire a produit zéro Versand, appel mock ou `.eml`. Le Versand
ID `3`, le PDF ID `10`, le volume et MySQL80 sont restés inchangés.

Preuve de limite cumulée HOME le 28 juillet 2026 : deux PDF synthétiques de
l'Auftrag `00991001`, de 648 et 661 octets, sont passés de `DISCOVERED` à
`READY`. Avec une limite HOME temporaire de 1 308 octets pour un total de
1 309 octets, un seul Versand ID `8` a été enregistré en `MANUAL_REVIEW` avec
ses deux liaisons et `versuch_anzahl=0`. Aucun appel au fournisseur mock, aucun
`.eml`, aucun découpage et aucun passage `ACCEPTED` n'ont eu lieu. La relance,
puis l'exécution avec la limite normale restaurée, ont chacune produit zéro
nouveau Versand, appel mock ou `.eml`. L'incident reste visible avec le total,
la limite et l'interdiction de retry automatique. Les 51 tests unitaires et
les 29 tests MySQL 8.4 sont verts. La configuration temporaire a été supprimée
et le conteneur HOME arrêté avec son volume conservé, sans changement de
MySQL80.

Validation finale HOME exécutée le 28 juillet 2026 : Windows 11, Python 3.14.6,
Docker 29.6.2, Compose 5.3.1 et MySQL 8.4.10 ont été contrôlés. `compileall`,
les 51 tests unitaires et les 29 tests MySQL isolés sont verts. Le healthcheck
de la base HOME conservée est `READY`; les quatre migrations sont présentes,
les douze tables sont InnoDB, les contraintes d'unicité et les liaisons sont
cohérentes, les trois verrous sont libres et aucun travail automatique n'est en
attente. Une exécution anti-doublon a produit zéro Versand, appel mock ou `.eml`;
les empreintes MySQL et `.eml` sont restées identiques. Le conteneur HOME a été
remis à l'arrêt avec son volume conservé et MySQL80 inchangé. À cette date, le
crash après appel fournisseur et la reconstruction du paquet de transfert
restaient ouverts.

Preuve de crash après fournisseur le 30 juillet 2026 : un PDF synthétique a
été traité dans une base MySQL `_test` séparée sur le serveur HOME 3307. Après
le retour du mock et la création d'un unique `.eml`, l'arrêt simulé a laissé le
Versand et le Prüfprotokoll en `SENDING`, tentative `1`, sans `accepted_at`.
Une nouvelle connexion a converti une seule fois les deux états en
`MANUAL_REVIEW`, créé l'audit
`MOCK_PROVIDER_RESULT_AMBIGUOUS_AFTER_CRASH` et retrouvé le `.eml` par son
`correlation_id`, sans appeler le mock. Le passage suivant a conservé un seul
Versand, un seul `.eml` et le même nombre d'audits. Les `52/52` tests unitaires
et `30/30` tests MySQL 8.4 sont verts. La base `_test` a été supprimée ; la
base HOME conservée, ses IDs `3`, `8` et `10`, et ses compteurs sont inchangés.

### H6 — Qualité et paquet de transfert

- [x] Couvrir tous les scénarios HOME de `docs/TEST_PLAN.md`.
- [x] Conserver les sorties CLI JSON structurées ; aucun fichier log HOME
  persistant n'est créé, la rotation éventuelle reste une décision COMPANY.
- [x] Vérifier les scripts PowerShell HOME et ajouter le constructeur du paquet.
- [x] Générer un paquet par liste blanche sans données générées ni secrets.
- [x] Mettre à jour les guides.

Critère de passage : tests verts, paquet contrôlé, aucune donnée réelle.

## Phase COMPANY

### C1 — Audit en lecture seule

Utiliser `docs/COMPANY_DISCOVERY_CHECKLIST.md`. Aucune installation.

### C2 — Décisions IT

Valider le PC/serveur, MySQL, le chemin UNC, le compte technique, Graph ou
SMTP, les secrets, les sauvegardes et les règles DSGVO.

### C3 — Installation autorisée

Installer avec sauvegarde, retour arrière, compte à moindre privilège et
`dry_run = true`.

### C4 — Test interne et pilote

Tester vers une boîte interne, puis deux cycles hebdomadaires sur quelques
Baustellen. Comparer avec le processus manuel.

### C5 — Production progressive et remise

Activer par petits groupes, surveiller, former un remplaçant et remettre le
runbook, le rapport de tests et la procédure de restauration.
