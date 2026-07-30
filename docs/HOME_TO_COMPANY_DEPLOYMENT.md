# Transfert HOME vers COMPANY

## Avant de quitter HOME

1. exécuter tous les tests ;
2. mettre à jour `PROJECT_STATUS.md`, `DECISIONS.md` et `EXECUTION_PLAN.md` ;
3. supprimer du paquet les secrets, `.env`, logs, e-mails, sauvegardes et PDF ;
4. inclure seulement les données synthétiques ;
5. produire une liste des fichiers et une somme SHA-256 du paquet ;
6. utiliser uniquement un moyen de transfert autorisé par l'entreprise.

Le paquet validé est accompagné de `Pruefversand_HOME_2026-07-30.zip.sha256`.
Comparer cette somme au ZIP avant extraction, puis conserver
`PACKAGE_MANIFEST.sha256` avec les sources. La racine HOME d'origine n'est pas
un dépôt Git : le manifeste prouve l'intégrité des fichiers transférés, pas un
historique de commits.

## Première ouverture sur COMPANY

Ne rien installer. Effectuer uniquement l'audit de
`COMPANY_DISCOVERY_CHECKLIST.md`, puis préparer un rapport des différences.

## Installation après accord

1. sauvegarder l'état initial ;
2. créer un dossier dédié et un compte technique ;
3. installer Python et les dépendances approuvées ;
4. créer MySQL et appliquer les migrations ;
5. configurer le NAS en lecture ;
6. configurer la messagerie vers une boîte interne ;
7. garder `dry_run = true` ;
8. exécuter les healthchecks et tests d'intégration ;
9. créer la tâche Windows seulement après validation ;
10. conserver un plan de désinstallation et de retour arrière.

## Passage au vrai envoi

Le vrai envoi n'est activé qu'après :

- test interne réussi ;
- validation des Empfänger ;
- validation du modèle d'e-mail ;
- sauvegarde et restauration testées ;
- approbation métier et IT ;
- plan pilote défini.
