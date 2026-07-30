# État du projet

Dernière mise à jour : 30 juillet 2026

## État global

Phase HOME clôturée pour transfert. La logique et les tests MySQL 8.4 sont
validés avec des données synthétiques, le paquet final est vérifié et l'absence
de dépôt Git est documentée. Aucun accès COMPANY et aucun vrai envoi n'ont eu
lieu.

## Terminé

- contexte, document de mission et plan antérieur inspectés ;
- exigences du MVP fixées ;
- décisions HOME/COMPANY consignées ;
- architecture simple sans interface Web définie ;
- configuration HOME sûre définie ;
- CSV synthétique préparé ;
- modèle MySQL initial défini ;
- logique initiale de validation CSV/PDF et de scan préparée ;
- tests unitaires initiaux préparés.
- inspection HOME en lecture seule réalisée : Python, Docker/Compose, MySQL,
  port 3306 et Git ;
- adaptateur transactionnel d'import CSV MySQL ajouté ;
- migration et import idempotent testés sur MySQL 8.4 éphémère ;
- rollback d'un CSV invalide et non-suppression des Empfänger absents testés ;
- port Compose HOME déplacé sur `127.0.0.1:3307` par défaut et configurable ;
- commande CLI `import-csv` ajoutée et testée réellement contre MySQL 8.4 ;
- observations du scanner persistées avec `mtime_ns` exact ;
- transition `DISCOVERED` puis `READY` après deux scans stables validée ;
- commande `demo-home` ajoutée et exécutée avec MySQL Docker sur 3307 ;
- Versand mock visualisable créé avec un PDF synthétique joint.
- relance `demo-home` dédupliquée dans MySQL sans second `.eml` ;
- ancien `.eml` mock rapproché dans une nouvelle base éphémère sans copie.
- démonstration `demo-manual-review-home` validée : Auftrag inconnu et nom
  ambigu en `MANUAL_REVIEW`, sans Versand ni nouveau `.eml`.
- commandes `manual-review list`, `assign` et `ignore` ajoutées ; confirmation
  obligatoire avant écriture, journal MySQL daté et PDF NAS conservé.
- commande `prepare-mock-versand` ajoutée : sélection MySQL des PDF `READY`,
  revalidation locale, pièce jointe mock et exclusion via `versand_protokoll`.
- option `scan --path` limitée au NAS HOME simulé pour isoler un scénario sans
  parcourir ni modifier d'autres emplacements ;
- option `prepare-mock-versand --at` ajoutée à la commande existante pour
  simuler un instant hebdomadaire avec décalage UTC explicite ;
- Versand hebdomadaire groupé validé : un Auftrag, deux nouveaux PDF, un seul
  Versand et un seul `.eml`, puis zéro création au second passage de la semaine.
- timeout ambigu du fournisseur mock validé sans réseau : Versand et PDF en
  `MANUAL_REVIEW`, raison et audit MySQL persistés, aucun `.eml` et aucun retry
  lors de la seconde exécution.
- commande `versand-review list` ajoutée pour les incidents fournisseur, avec
  transaction MySQL `READ ONLY`, Versand ID, Auftrag, raison, date, PDF liés et
  indicateur explicite de blocage du retry ;
- `manual_review_home.ps1 -Action List` corrigé : aucune base éphémère, démo,
  import CSV ou scan ; `VersandList` consulte séparément les incidents Versand.
- migration `004_versand_review_decision.sql` ajoutée et appliquée à la base
  HOME conservée ; le Versand ID `3` reste inchangé et sans décision ;
- actions `VersandAccept`, `VersandRetry` et `VersandKeep` ajoutées avec
  confirmation préalable, journal MySQL, contrôles HOME et séparation complète
  de `manual-review assign/ignore` ;
- retry mock manuel limité à une tentative, avec contrôle NAS, stabilité,
  taille, SHA-256, Empfänger et anti-doublon avant création locale du `.eml`.
- commande `manual-versand record` ajoutée avec confirmation `OUI`, contrôles
  HOME, revalidation du PDF et écriture transactionnelle du Versand urgent ;
- cycle urgent HOME validé : PDF ID `7` en `MANUALLY_SENT` sans `.eml`, second
  PDF seul dans le Versand hebdomadaire, puis relance anti-doublon sans création.
- persistance pré-fournisseur ajoutée : Versand `PLANNED`, liaisons PDF et audit
  durable avant le mock, avec reprise limitée à `versuch_anzahl=0` ;
- interruption HOME avant fournisseur validée sur le Versand ID `6`, puis reprise
  Python unique et conservation après redémarrage du conteneur MySQL 3307.
- mise à jour CSV HOME validée avec zéros initiaux, Auftrag entièrement omis
  conservé actif avec ses Empfänger, compteurs explicites de suppression et
  désactivation à zéro, puis réimport bloqué par le SHA-256.
- désactivation explicite HOME validée : `00991003`, sa Baustelle et son
  Empfänger conservés sous les mêmes IDs mais inactifs ; PDF stable bloqué en
  `MANUAL_REVIEW` et deux cycles sans Versand, appel mock ou `.eml`.
- concurrence HOME validée avec deux vrais processus de scan puis deux vrais
  processus de Versand ; les concurrents sans verrou quittent proprement avec
  `SKIPPED_LOCK_BUSY` et ne produisent ni observation ni appel mock en plus.
- indisponibilités HOME validées : NAS absent et MySQL Docker arrêté donnent un
  healthcheck `NOT_READY` borné et explicite ; récupération sur le même volume,
  états historiques et anti-doublon contrôlés sans envoi ni suppression.
- crash après fournisseur validé sur une base `_test` séparée : résultat
  `SENDING` converti en `MANUAL_REVIEW`, `.eml` mock retrouvé, audit durable et
  aucun retry automatique ;
- paquet HOME final construit par liste blanche avec manifeste SHA-256 interne
  et somme externe ;
- absence de dépôt Git documentée sans créer d'historique artificiel.

## En cours

- aucun développement HOME en cours ; phase COMPANY non démarrée.

## Prochain travail

1. transférer le paquet par un moyen autorisé ;
2. commencer uniquement l'audit COMPANY en lecture seule après accord.

## Blocages externes

- le client CLI `mysql` est absent du `PATH`, mais le connecteur Python 9.7.0
  et l'image locale `mysql:8.4` sont disponibles ;
- un service Windows `MySQL80` existe sur le poste et utilisait historiquement
  3306. Il a été observé `Stopped / Manual` le 30 juillet sans être modifié ;
  Compose HOME reste limité à 3307 ;
- cette racine n'est pas un dépôt Git ; cette absence est désormais documentée
  et l'initialisation reste réservée au poste cible autorisé ;
- format réel du CSV non fourni ;
- jour et heure réels non validés ;
- format futur garanti des PDF non validé ;
- messagerie COMPANY inconnue ;
- disponibilité du PC COMPANY inconnue.

Ces points ne bloquent pas le développement sûr de la logique HOME.

## Preuves de test

- `python -m unittest discover -s tests -v` : 52 tests réussis.
- suite `tests_mysql` : 30 tests réussis dans une base temporaire `_test` sur
  le serveur HOME MySQL 8.4.10, puis base temporaire supprimée.
- migrations `001`, `002`, `003` et `004` appliquées deux fois sans doublon de
  version.
- même CSV synthétique importé deux fois : 2 Auftrag, 3 Empfänger et 1 rapport
  d'import au total ; `00125083` conserve ses zéros initiaux.
- CSV synthétique invalide : rollback complet vérifié.
- test CLI réel : `import-csv`, premier `scan` en `DISCOVERED`, second `scan`
  en `READY`, toujours avec `dry_run=true` et fournisseur `mock`.
- PDF modifié entre deux scans : compteur de stabilité remis à 1.
- Auftrag inconnu après deux scans : `MANUAL_REVIEW`, jamais `READY`.
- `demo-home` : premier scan `DISCOVERED`, second `READY`, un `.eml` créé
  avec un seul Empfänger `example.invalid` et une pièce jointe PDF valide.
- relance sur la même base : `new_message_count=0`, Auftrag `00125083` ignoré,
  compte `.eml` stable à 1 avant et après les deux appels.
- preuve visuelle HOME :
  `var/mock_mail/20260726T171857Z_287151a3-5783-445d-a0ae-a0c9ad790dd1.eml`.
- `demo-manual-review-home` : `999999_DEMO_Unbekannt.pdf` et `F0661.pdf`
  classés `DISCOVERED` puis `MANUAL_REVIEW`; Versand 0 avant/après, compteur
  `.eml` inchangé à 1 et `new_message_count=0`.
- `manual-review list` exécuté réellement sur Docker 3307 : 2 PDF synthétiques,
  IDs, noms, raisons et `stable_scan_count=2` affichés, aucun nouvel `.eml`.
- tests MySQL `assign`/`ignore` : `00125083` conservé, Auftrag inactif refusé,
  SHA-256 modifié refusé, décision datée, fichier conservé et Versand à 0.
- les actions interactives `assign` et `ignore` n'ont pas été lancées sur la
  démonstration HOME sans confirmation explicite de l'utilisateur.
- cycle MANUAL_REVIEW → Versand validé le 27 juillet 2026 : `F0661.pdf`
  associé à `00125083`, premier passage avec un `.eml` et une pièce jointe,
  second passage avec `new_message_count=0` et `already_processed_count=1`.
- nouvel `.eml` mock :
  `var/mock_mail/20260727T141858Z_5c5a4bb8-d73b-4d05-add1-848b7dd5046a.eml` ;
  sujet `[TEST]`, Empfänger `example.invalid` et signature PDF contrôlés.
- Versand hebdomadaire HOME simulé à `2026-07-31T16:00:00+02:00` : deux scans
  donnant deux `DISCOVERED`, puis deux `READY`; un seul Versand de période
  `2026-07-27` et un seul nouveau `.eml` avec
  `00125083_WOCHE_2026_07_A.pdf` et `00125083_WOCHE_2026_07_B.pdf`.
- preuve `.eml` groupée :
  `var/mock_mail/20260727T145835Z_3220ae65-557b-4ba8-800e-1484fcdcf967.eml` ;
  analyse MIME locale : exactement deux pièces jointes, Empfänger uniquement
  sous `example.invalid`, aucun `F0661.pdf`.
- deuxième exécution de la même semaine : `weekly_due=false`, zéro nouveau
  Versand et zéro nouveau `.eml`; `F0661.pdf` reste `ACCEPTED`.
- timeout mock HOME sur `00125083_TIMEOUT_AMBIGU_20260727.pdf` : compte Versand
  `2 → 3`, Versand `MANUAL_REVIEW` `0 → 1`, PDF `MANUAL_REVIEW` `1 → 2`, audit
  timeout `0 → 1`, `.eml` stable à `3`; `accepted_at` vide et tentative à `1`.
- relance après timeout : zéro PDF `READY`, zéro nouvel incident, zéro nouveau
  Versand, audit ou `.eml`.
- consultation read-only validée sur la base HOME conservée : le Versand ID `3`
  de `00125083` est affiché avec sa raison, sa date, `automatic_retry_blocked`
  à `true` et le Prüfprotokoll lié ID `5`. `manual-review list` n'affiche plus
  cet incident et reste réservé aux problèmes d'identification PDF. Empreinte
  MySQL et compteur `.eml` (`3`) identiques avant/après les deux listes.
- résolution Versand testée sur des incidents synthétiques isolés : confirmation
  `ACCEPTED` sans `.eml`, `KEEP_UNKNOWN` sans changement de statut, un seul
  retry mock passant la tentative de `1` à `2`, puis second retry refusé.
- PDF modifié après timeout : retry refusé avant décision et avant `.eml` grâce
  au contrôle SHA-256 ; les statuts restent `MANUAL_REVIEW`.
- essai réel du lanceur sur le Versand ID `3` avec réponse `NON` :
  `CANCELLED`, `modified=false`, zéro décision et `.eml` stable à `3`.
- Versand urgent HOME confirmé sur le Prüfprotokoll ID `7` : Versand ID `4`,
  statut `MANUALLY_SENT`, SHA-256, date, opérateur `Utilisateur HOME`, raison et
  Empfänger `example.invalid` persistés ; fichier PDF conservé et compteur
  `.eml` stable à `3` pendant l'enregistrement.
- Versand hebdomadaire simulé à `2026-08-07T16:00:00+02:00` : un seul nouveau
  Versand ID `5` et un seul nouvel `.eml`, contenant exactement
  `00125083_URGENT_HOME_AUTOMATIC_20260803.pdf`; le PDF urgent n'est pas joint.
- preuve `.eml` urgente :
  `var/mock_mail/20260727T173546Z_47a25aef-4ddf-4c7d-9cb9-20e743a23349.eml` ;
  analyse MIME locale : une pièce jointe et toutes les adresses sous
  `example.invalid`.
- relance de la période `2026-08-03` : `weekly_due=false`, zéro nouveau Versand
  et zéro nouvel `.eml`; PDF ID `6` `ACCEPTED`, PDF ID `7` `MANUALLY_SENT`.
- le Versand ID `3` reste `MANUAL_REVIEW` avec la décision `KEEP_UNKNOWN` et la
  raison enregistrée `Resultat fournisseur toujours inconnu`.
- reprise HOME sur `00125083_REPRISE_HOME_20260810.pdf` : compte `.eml`
  `4 → 4 → 5 → 5`; après interruption, Versand ID `6` `PLANNED`, tentative `0`,
  PDF ID `8` `SENDING`, aucun audit d'appel fournisseur. Après reprise, Versand
  et PDF `ACCEPTED`, tentative `1`, exactement un audit d'appel et un `.eml`.
- preuve `.eml` de reprise :
  `var/mock_mail/20260727T180341Z_5d049402-2185-4718-820a-1de282f40031.eml` ;
  analyse MIME locale : exactement `00125083_REPRISE_HOME_20260810.pdf`,
  adresses uniquement sous `example.invalid` et corrélation identique au
  Versand durable.
- relance de la période `2026-08-10` avant et après redémarrage du conteneur :
  `weekly_due=false`, `provider_call_count=0`, zéro nouveau Versand et zéro
  nouvel `.eml`. Volume conservé, MySQL80 resté actif sur 3306 avec le même PID.
- CSV HOME 1 : `00991001` et `00991002` créés comme chaînes avec leurs zéros
  initiaux ; résultat 2 insérés, 0 mis à jour, 0 supprimé, 0 désactivé.
- CSV HOME 2 : `00991001` mise à jour sans duplication, `00991003` ajoutée et
  `00991002` absente du fichier mais toujours active avec ses deux Empfänger
  actifs ; résultat 1 inséré, 1 mis à jour, 0 supprimé, 0 désactivé.
- réimport du CSV HOME 2 : `ALREADY_IMPORTED`, même SHA-256
  `0d03bcd8cd49acd9d8f291adf38507295b58cb989f129be5a99ca22b6eeacab3`,
  0 insertion et 0 mise à jour ; `csv_import` reste à trois entrées au total.
- avant/après les imports : Prüfprotokolle `8`, Versand `6`, liaisons `7`,
  décisions PDF `1`, décisions Versand `1`, audits `10` et `.eml` `5`.
  Les empreintes SHA-256 de ces trois ensembles sont identiques et le Versand
  ID `3` reste `MANUAL_REVIEW` avec `KEEP_UNKNOWN`.
- désactivation `00991003` : Auftrag ID `5`, Baustelle ID `5` et Empfänger ID
  `7` toujours présents, désormais `aktiv=0`; `00991001` et `00991002` restent
  byte-for-byte inchangés dans l'empreinte de contrôle.
- PDF synthétique `00991003_INACTIVE_HOME_20260817.pdf`, ID `9`, SHA-256
  `a45bae6545d85c144e91e68f15241e8d329a8332b62c13cc5ad05dfb2bbc0858` :
  premier scan `DISCOVERED`, deuxième et troisième scans `MANUAL_REVIEW`, raison
  `Auftrag inconnu ou inactif`, aucune liaison Versand et jamais `ACCEPTED`.
- cycles hebdomadaires simulés à `2026-08-21T16:00:00+02:00` : zéro document
  READY, zéro appel mock, zéro nouveau Versand et `.eml` stable à `5`.
- réimport du CSV de désactivation : `ALREADY_IMPORTED`, SHA-256
  `abc1653aa40b26c8d35811ea50615f19eab478cce9ca7b54460baf6b68248075`,
  zéro insertion, mise à jour, suppression ou nouvelle désactivation.
- concurrence scan sur `00991001_CONCURRENT_HOME_20260824.pdf` : un processus
  `SCAN_COMPLETE` et un processus `SKIPPED_LOCK_BUSY`; MySQL contient une seule
  ligne Prüfprotokoll ID `10`, un seul SHA-256
  `8a2b723e32fbb517cfeccd4278ff94f1f9fa4f38bffd72346e48385ac5296214` et
  `stable_scan_count=1`. Le scan normal suivant a donné `READY` et
  `stable_scan_count=2`.
- concurrence Versand à `2026-08-28T16:00:00+02:00` : un processus
  `MOCK_VERSAND_PREPARED` et un processus `SKIPPED_LOCK_BUSY`; compteurs
  Versand `6 → 7`, liaisons `7 → 8`, appels fournisseur mock `1 → 2` et `.eml`
  `5 → 6`. Le Versand ID `7` possède exactement une liaison et exactement un
  audit `MOCK_PROVIDER_CALL_STARTED`.
- nouvel `.eml` mock :
  `var/mock_mail/20260727T235615Z_31b57d8a-0ae2-4b99-9a27-403d01f3eb75.eml` ;
  analyse MIME locale : exactement `00991001_CONCURRENT_HOME_20260824.pdf` et
  l'unique Empfänger `anna.991001@example.invalid`.
- relance normale de la période `2026-08-24` : `weekly_due=false`, zéro nouveau
  Versand, liaison, appel mock ou `.eml`. Les verrous scan et Versand sont
  libres ; le PDF ID `10` est `ACCEPTED` et le Versand ID `3` reste
  `MANUAL_REVIEW` avec `KEEP_UNKNOWN`.
- NAS synthétique absent : `healthcheck` code `1` en `385 ms` avec
  `NAS_UNAVAILABLE`; `scan --dry-run` code `2` en `215 ms` avec l'unique erreur
  `Dossier NAS introuvable`. Retour sur la configuration normale : code `0`,
  NAS et MySQL 8.4.10 `AVAILABLE`, statut `READY`.
- MySQL Docker arrêté : port 3307 sans écoute, `healthcheck` code `1` en
  `2396 ms`, erreur structurée `MYSQL_UNAVAILABLE`, aucun stderr ni traceback.
  Le scan dry-run a terminé en `240 ms` avec
  `DRY_RUN_COMPLETE_NO_PERSISTENCE`, `database_checked=false` et
  `persistence_performed=false`.
- après redémarrage : même conteneur, même volume
  `5f4a0bd48843b2d7018845f076ec54f54f99708c592bc764fe07e15f5eeaa125`,
  `mysqladmin` prêt et healthcheck `READY` en `393 ms`. Les verrous scan,
  Versand et import CSV sont libres.
- compteurs avant/après identiques : Prüfprotokolle `10`, Versand `7`, liaisons
  `8`, appels mock `2`, audits `13` et `.eml` `6`. L'empreinte combinée de
  toutes les tables reste
  `6a4b79b07eab940d10b69421491e4143f4221f579ae638817abe53b84d6b0368`.
  Le Versand ID `3` reste `MANUAL_REVIEW / KEEP_UNKNOWN` et le PDF ID `10`
  reste `ACCEPTED` avec `stable_scan_count=2`.
- relance anti-doublon après récupération : `weekly_due=false`, zéro nouveau
  Versand, appel mock ou `.eml`. Le conteneur HOME a retrouvé son état arrêté,
  volume conservé ; MySQL80 est resté `Running`, PID service `17084` et PID
  d'écoute 3306 `21044`.
- qualité finale du jalon indisponibilités : `compileall` réussi, 49 tests
  unitaires réussis et 27 tests MySQL 8.4 réussis, dont le probe MySQL en lecture
  seule. `healthcheck`, validation du CSV synthétique, scan `--dry-run` et
  `docker compose config --quiet` sont également réussis.
- après l'audit final, le conteneur MySQL HOME du projet a été arrêté proprement
  sans supprimer son volume. Le service Windows `MySQL80` et son écoute sur
  3306 ont conservé exactement leurs états et PID.
- `docker compose config --quiet` : configuration valide avec des secrets HOME
  synthétiques temporaires et non affichés.
- `healthcheck` : `READY` exige désormais le NAS disponible et une requête MySQL
  en lecture réussie ; pilote présent mais serveur arrêté donne `NOT_READY`.
- validation du CSV synthétique : 3 lignes valides, aucune erreur.
- scan dry-run du faux NAS : PDF synthétique détecté, SHA-256 calculé,
  première observation classée `DISCOVERED`.
- contrôle du fichier d'exemple `F0661.pdf` : conteneur PDF valide, mais aucune
  Auftragsnummer fiable dans le nom ; retour de contrôle manuel attendu.
- le conteneur de test MySQL a été supprimé après l'exécution ; aucun service
  existant et aucune donnée réelle n'ont été modifiés.
- limite cumulée HOME sur `00991001` : deux nouveaux PDF IDs `11` et `12`,
  tailles `648` et `661` octets, total `1 309`, limite temporaire `1 308` ;
  deux scans ont donné `DISCOVERED`, puis `READY`.
- Versand ID `8` : `MANUAL_REVIEW`, tentative `0`, deux liaisons et raison
  persistée avec total et limite. Audit `ATTACHMENT_LIMIT_EXCEEDED` :
  `provider_invoked=false`, `message_accepted=false`, `eml_created=false`,
  `split_performed=false` et `automatic_retry=false`.
- compteurs du jalon : Prüfprotokolle `10 → 12`, Versand `7 → 8`, liaisons
  `8 → 10`, audits `13 → 14`, appels mock stables à `2` et `.eml` stables à
  `6`. Aucun nouveau PDF n'est `ACCEPTED`.
- seconde invocation avec la limite temporaire, puis invocation avec la
  configuration HOME normale restaurée : chacune a produit zéro document
  READY, zéro nouveau Versand, zéro appel mock et zéro `.eml`; le lot n'a pas
  été débloqué par le seul changement de configuration.
- les contrôles de frontière couvrent désormais un total exactement égal à la
  limite, autorisé, et un total supérieur d'un octet, bloqué avant fournisseur.
  Versand ID `3` reste `MANUAL_REVIEW / KEEP_UNKNOWN`, PDF ID `10` reste
  `ACCEPTED`, et les empreintes des anciens enregistrements restent inchangées.
- qualité finale du jalon limite cumulée : `compileall` réussi, `51/51` tests
  unitaires et `29/29` tests MySQL 8.4 réussis. `healthcheck` est `READY`, la
  validation CSV compte trois lignes valides, le scan dry-run confirme
  `database_checked=false` et `persistence_performed=false`, et Compose est
  valide.
- la configuration temporaire a été supprimée ; `config/home.example.toml` a
  conservé le SHA-256
  `D37E528CC6935B2C24D71755622CE09A91658E8A1CDB889AE165FB5255A39118`.
  Le conteneur HOME a été arrêté sans supprimer le volume
  `5f4a0bd48843b2d7018845f076ec54f54f99708c592bc764fe07e15f5eeaa125`.
  MySQL80 est resté actif avec les mêmes PID de service (`17084`) et d'écoute
  3306 (`21044`).
## Validation finale HOME — 28 juillet 2026

Validateur : Codex, environnement HOME uniquement.

- environnement : Windows 11 Professionnel build `26200`, PowerShell `5.1`,
  Python `3.14.6`, `mysql-connector-python 9.7.0`, Docker `29.6.2`, Compose
  `5.3.1` et image locale `mysql:8.4` ; aucune installation effectuée ;
- sécurité locale : `dry_run=true`, provider `mock`, NAS
  `sample_data/nas_mock`, port `3307`, aucun `.env` réel, aucun fichier dans
  `upload/`, aucun code SMTP/Graph détecté dans `src/` ou `scripts/` ;
- artefacts synthétiques : 15 PDF NAS valides, quatre CSV totalisant neuf
  lignes et neuf adresses `example.invalid`, six `.eml` `[TEST]` contenant sept
  pièces jointes PDF valides et aucun Empfänger hors domaine autorisé ;
- qualité : `compileall` réussi, `51/51` tests unitaires et `29/29` tests MySQL
  8.4 réussis, CSV exemple `3/3`, scan dry-run sans connexion MySQL ni
  persistance, et `docker compose config --quiet` réussi ;
- indisponibilité attendue : conteneur HOME arrêté, `healthcheck` code `1` avec
  `NOT_READY / MYSQL_UNAVAILABLE`, sans faux succès ;
- disponibilité contrôlée après confirmation : `healthcheck` code `0`, statut
  `READY`, NAS et MySQL `AVAILABLE`, serveur `8.4.10` ; quatre migrations,
  douze tables toutes InnoDB, zéro SHA-256 ou Auftragsnummer dupliqué, zéro
  liaison orpheline, zéro Empfänger hors `example.invalid` et trois verrous
  libres ;
- état conservé : 12 Prüfprotokolle (`ACCEPTED:6`, `MANUAL_REVIEW:5`,
  `MANUALLY_SENT:1`) et huit Versand (`ACCEPTED:5`, `MANUAL_REVIEW:2`,
  `MANUALLY_SENT:1`). Versand ID `3` reste `MANUAL_REVIEW / KEEP_UNKNOWN`, PDF
  ID `10` reste `ACCEPTED`, et Versand ID `8` reste bloqué avec tentative zéro ;
- anti-doublon final : zéro document `READY`, zéro nouveau Versand, appel mock,
  message accepté ou `.eml`. Empreinte des données MySQL avant/après :
  `4ceb80595bfb56a320bfd5a93c0d9ab3d5a350879665294cb3bc4da785ccc615` ;
  empreinte du manifeste des six `.eml` avant/après :
  `eaf76c4b97c41c46a7f022f64dc6decc0bf2b8d410494b90690d6927c7e18793` ;
- restauration : conteneur HOME arrêté, port 3307 libre, volume
  `5f4a0bd48843b2d7018845f076ec54f54f99708c592bc764fe07e15f5eeaa125`
  conservé ; MySQL80 reste `Running` avec les PID `17084` et `21044` inchangés.

Réserves enregistrées le 28 juillet : le crash après l'appel fournisseur restait
à tester. L'archive
`Pruefversand_HOME_2026-07-26.zip` est antérieure aux jalons actuels et ne doit
pas être utilisée comme paquet de transfert final. La racine n'est toujours pas
un dépôt Git. Ces trois points empêchaient alors de déclarer H6 ou COMPANY prêts.

## Clôture HOME pour transfert — 30 juillet 2026

Validateur : Codex, environnement HOME uniquement.

- crash après fournisseur : un processus séparé simulé a créé un unique `.eml`
  mock puis s'est arrêté avant `ACCEPTED`. MySQL a conservé le Versand et le
  Prüfprotokoll en `SENDING`, tentative `1`. Une nouvelle connexion les a passés
  une seule fois en `MANUAL_REVIEW`, avec l'audit
  `MOCK_PROVIDER_RESULT_AMBIGUOUS_AFTER_CRASH`, puis un troisième passage a
  produit zéro appel mock, zéro Versand, zéro audit et zéro `.eml` en plus ;
- qualité actualisée : `compileall` réussi, `52/52` tests unitaires et `30/30`
  tests MySQL 8.4 réussis. La base temporaire suffixée `_test` a été supprimée ;
- état HOME historique avant/après : 12 Prüfprotokolle, huit Versand, dix
  liaisons et 14 audits. Versand ID `3` reste
  `MANUAL_REVIEW / KEEP_UNKNOWN`, Versand ID `8` reste `MANUAL_REVIEW` avec
  tentative zéro et PDF ID `10` reste `ACCEPTED` ;
- paquet : `Pruefversand_HOME_2026-07-30.zip` est généré par
  `scripts/build_home_package.ps1`, avec manifeste SHA-256 revérifié depuis le
  ZIP et somme externe `Pruefversand_HOME_2026-07-30.zip.sha256`. Aucun secret,
  `.env`, PDF, `.eml`, log, cache, `upload/`, `var/` ou ancien ZIP n'est inclus ;
- Git : `.git` reste absent. La décision D-044 documente cette absence et
  réserve `git init` à un poste cible autorisé ;
- restauration : le conteneur HOME est `exited`, le volume
  `5f4a0bd48843b2d7018845f076ec54f54f99708c592bc764fe07e15f5eeaa125`
  est conservé et les ports 3306/3307 sont libres. `MySQL80` a été observé
  `Stopped / Manual` avant et après l'essai et n'a jamais été modifié.

HOME est prêt pour un transfert contrôlé. Cette clôture n'autorise ni
installation COMPANY, ni secret réel, ni activation d'un fournisseur réel.
