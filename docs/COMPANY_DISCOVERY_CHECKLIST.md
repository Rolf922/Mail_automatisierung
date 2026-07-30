# Checklist d'audit COMPANY en lecture seule

## Machine

- [ ] Version et édition de Windows
- [ ] Architecture 64 bits
- [ ] Python installé, version et origine
- [ ] PowerShell et politique d'exécution
- [ ] Docker autorisé ou interdit
- [ ] Espace disque
- [ ] Fuseau horaire et synchronisation
- [ ] Veille, arrêt nocturne et redémarrage automatique
- [ ] Compte de service disponible
- [ ] Antivirus, proxy et pare-feu
- [ ] Accès Internet et dépôts de paquets autorisés

## NAS Synology

- [ ] Modèle et DSM
- [ ] Chemin UNC exact
- [ ] SMB2/SMB3 et chiffrement
- [ ] Droits de lecture du compte technique
- [ ] Volumes, sous-dossiers et quantité de PDF
- [ ] Convention actuelle et future des noms
- [ ] Comportement pendant la copie
- [ ] Sauvegarde du NAS

## MySQL

- [ ] Version et emplacement du serveur
- [ ] Méthode d'administration
- [ ] TLS
- [ ] Compte dédié et droits minimaux
- [ ] Politique de sauvegarde
- [ ] Base de test disponible
- [ ] Supervision et espace

## Messagerie

- [ ] Microsoft 365, Exchange, SMTP relay ou autre
- [ ] Méthode d'authentification approuvée
- [ ] Adresse d'expéditeur
- [ ] Boîte interne de test
- [ ] Limites de pièces jointes et de débit
- [ ] Journaux ou preuve d'acceptation disponibles
- [ ] Gestion des bounces
- [ ] Procédure de rotation des secrets/certificats

## Métier

- [ ] CSV réel anonymisé et définition des colonnes
- [ ] Jour et heure hebdomadaires
- [ ] Modèle d'objet et corps
- [ ] Destinataires TO/CC/BCC
- [ ] Règle des corrections
- [ ] Responsable de la quarantaine
- [ ] Procédure d'envoi urgent
- [ ] Périmètre du pilote
- [ ] Durée de conservation

À la fin, produire un rapport. Ne modifier aucun réglage pendant cette étape.

