# FleetUele

Application locale de traçabilité, suivi de maintenance, carburant et gestion d’abonnements de flotte. Elle fonctionne sans connexion Internet après installation et conserve les données dans SQLite.

## Prérequis
- Python 3.10 ou plus récent
- Une installation initiale des bibliothèques Flask et ReportLab (`pip install -r requirements.txt`)

## Lancement rapide

```bash
cd fleetuele
python3 -m venv .venv
source .venv/bin/activate       # Windows : .venv\\Scripts\\activate
pip install -r requirements.txt
python app.py
```

Ouvrir ensuite **http://127.0.0.1:5000** dans le navigateur. La base `data/fleetuele.sqlite3` et les données de démonstration sont créées au premier lancement. La première installation des dépendances nécessite Internet; après installation, l’application ne charge aucune ressource distante. Sous Windows, le fichier `run_windows.bat` peut lancer l’application.

Sous Windows avec PowerShell, l’activation manuelle de l’environnement virtuel se fait avec `.venv\\Scripts\\Activate.ps1`.

## Démonstration préchargée
Cinq engins appartenant à deux clients sont inclus : une moto, un pick-up 4x4, un camion poids lourd, une motopompe et un groupe électrogène. Les catégories de tarifs $10/$50, les statuts actifs/expirés, pleins, interventions et paiements de démonstration sont préchargés dans SQLite. Les dates sont relatives au jour de l’initialisation de la base.

Pour repartir de zéro, arrêter le serveur puis supprimer `data/fleetuele.sqlite3`; la base de démonstration sera recréée au lancement.

## Fonctionnalités
- Tableau de bord avec flotte, dépenses, règlements et alertes de maintenance/expiration à J-5.
- Gestion CRUD des engins et attribution automatique de $10/mois (véhicule léger/moto) ou $50/mois (poids lourd/machine).
- Périodes d’abonnement par engin, statuts payé/en attente/expiré, renouvellement, règlements Cash/M-Pesa/Airtel Money/Orange Money et reçu/facture PDF.
- Saisie des pleins, coûts USD/CDF, taux de change et calcul L/100 km ou L/heure depuis deux relevés compteur.
- Carnet des vidanges, filtres, freins, pneus et réparations. Une intervention « Vidange » devient la nouvelle référence du suivi préventif.
- Rapport mensuel des règlements encaissés, coûts carburant/maintenance et solde net.
- Exports CSV (compatibles Excel, séparateur `;`) des engins, règlements, pleins et interventions.

## Sauvegarde et usage sur réseau local
La base de données est le fichier `data/fleetuele.sqlite3`. Pour la sauvegarder, arrêter l’application puis copier ce fichier. Par défaut, le serveur écoute uniquement sur l’ordinateur local. Pour l’ouvrir sur un réseau local de confiance, définir `FLEETUELE_HOST=0.0.0.0` avant le lancement et configurer le pare-feu. L’application n’intègre pas d’authentification multi-utilisateur; ne l’exposez pas directement à Internet.

## Tests
```bash
python3 -m unittest -v
```
