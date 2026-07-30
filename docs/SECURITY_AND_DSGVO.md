# Sécurité et DSGVO

## Règles techniques

- Comptes MySQL, NAS et mail à moindre privilège.
- Accès en lecture au dossier source NAS.
- TLS pour MySQL distant et la messagerie.
- Secrets hors du code et de Git.
- Logs sans corps d'e-mail, contenu PDF ni secret.
- Rotation et conservation des logs configurables.
- Sauvegarde MySQL et test de restauration.
- Aucun PDF stocké en BLOB.

## Règles HOME

- Ne jamais utiliser de noms, adresses ou PDF clients réels.
- Le fournisseur mock est obligatoire.
- Le suffixe `example.invalid` empêche une distribution réelle.
- `upload/`, `tmp/`, `var/`, `.env` et les logs sont ignorés par Git.
- Le PDF réel d'exemple sert seulement à comprendre le format ; il n'entre pas
  dans le faux NAS ni dans les tests.

## Points à valider avec l'entreprise

- base légale et finalité du traitement ;
- personnes autorisées à consulter les logs et l'historique ;
- durée de conservation ;
- procédure de correction et suppression selon les obligations applicables ;
- chiffrement, sauvegarde et localisation des données ;
- procédure d'incident et destinataire des alertes ;
- contrat et règles du fournisseur de messagerie ;
- méthode autorisée de transfert HOME vers COMPANY.

