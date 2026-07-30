# Exigences du MVP

Version : 26 juillet 2026

## Objectif validé

Créer un programme Python en ligne de commande qui :

1. importe les Baustellen, Aufträge et Empfänger depuis un CSV ;
2. conserve ces informations dans MySQL ;
3. scanne un dossier Windows sur le Synology NAS ;
4. contrôle et identifie les nouveaux Prüfprotokolle PDF ;
5. regroupe les PDF par Auftrag ;
6. prépare un envoi une fois par semaine ;
7. empêche les doubles envois ;
8. garde un historique exploitable ;
9. permet de déclarer un envoi urgent effectué manuellement.

Le MVP ne contient ni interface Web, ni application mobile, ni tableau de bord.

## Règles confirmées

### Import CSV

- L'Auftragsnummer est une chaîne ; `00125083` ne devient jamais `125083`.
- L'import accepte UTF-8 et UTF-8 avec BOM.
- Les espaces sont nettoyés et les e-mails sont validés.
- Le même CSV peut être importé plusieurs fois sans créer de doublons.
- Une ligne absente d'un nouvel import n'est pas supprimée.
- La désactivation est explicite avec `aktiv=0`.
- Un import réel est transactionnel : tout est validé avant la modification.

### Détection des PDF

- Le scanner est prévu toutes les 10 minutes.
- Seuls les fichiers portant l'extension `.pdf` sont examinés.
- Le contenu doit commencer par la signature PDF et se terminer correctement.
- Le fichier doit garder la même taille et la même date de modification pendant
  deux scans avant traitement.
- Le programme calcule une empreinte SHA-256.
- Le PDF reste sur le NAS ; MySQL ne contient pas de BLOB.

### Identification

- Format futur recommandé :
  `126021_F0661_Pruefbericht_Beton_2026-06-25.pdf`.
- Format minimal : `126021_F0661.pdf`.
- Le format est configurable par expression régulière.
- Un nom comme `F0661.pdf` ne suffit pas pour retrouver automatiquement
  l'Auftrag.
- Un mapping `Probe-Nr. → Auftragsnummer` pourra être ajouté s'il est fourni
  et administré par une source fiable.
- L'OCR ne peut proposer qu'une valeur à vérifier manuellement.

### Envoi

- L'envoi automatique a lieu une fois par semaine.
- Le jour et l'heure sont configurables.
- Un e-mail regroupe uniquement les nouveaux PDF d'un même Auftrag.
- Aucun e-mail n'est créé s'il n'existe aucun nouveau PDF.
- Un Auftrag inactif, inconnu, ambigu ou sans Empfänger bloque le lot.
- La liste TO/CC réellement utilisée est figée dans l'historique.
- Un PDF corrigé avec un nouveau contenu et un nouveau SHA-256 est une nouvelle
  version ; sa règle d'objet devra signaler qu'il s'agit d'une correction.
- Un timeout ambigu devient `MANUAL_REVIEW`.
- Les échecs certains pourront être retentés de manière limitée.
- `ACCEPTED` n'est pas présenté comme une preuve de livraison.

### Envoi manuel urgent

Un opérateur doit pouvoir déclarer qu'un ou plusieurs PDF ont été envoyés
manuellement. La trace contient :

- la personne ;
- la date ;
- le motif ;
- les destinataires si connus ;
- les PDF concernés.

Ces PDF ne doivent plus entrer dans un lot automatique.

### Sécurité HOME

- Données et adresses uniquement synthétiques.
- NAS simulé dans `sample_data/nas_mock`.
- Fournisseur `mock` uniquement.
- `dry_run = true` obligatoire.
- Domaines destinataires limités à `example.invalid`.
- Aucun secret, PDF client, log ou e-mail généré dans Git.

## Critères d'acceptation du MVP

- Le même CSV réimporté ne crée pas de doublons.
- Le même SHA-256 n'est jamais envoyé automatiquement deux fois.
- Un fichier encore copié n'est pas traité.
- Un faux PDF, un mauvais nom ou un Auftrag inconnu reste bloqué.
- Un lot contient les bons PDF et les bons Empfänger pour un seul Auftrag.
- Une panne suivie d'un redémarrage ne perd pas l'état et ne double pas l'envoi.
- Un envoi manuel urgent exclut correctement les PDF.
- Tous les tests automatisés sont réussis.
- Deux cycles hebdomadaires pilotes sont réussis dans l'entreprise.

## Questions non bloquantes pour HOME

Ces valeurs restent configurables et devront être validées en entreprise :

1. jour et heure exacts de l'envoi hebdomadaire ;
2. colonnes et encodage exacts du CSV réel ;
3. modèle d'objet et corps de l'e-mail ;
4. taille maximale réelle des pièces jointes ;
5. service de messagerie : Graph ou relais SMTP ;
6. règle métier d'une correction reçue après l'envoi hebdomadaire ;
7. format garanti par le futur générateur de PDF ;
8. personne responsable du contrôle de la quarantaine ;
9. politique de conservation des logs et de l'historique ;
10. disponibilité permanente du PC prévu.

