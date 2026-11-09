# Bot Vinted

Toutes les 5 minutes, GitHub lance le bot sur ses serveurs. Aucune machine n'a besoin de rester allumée chez toi.

À chaque passage, le bot :

1. regarde les nouvelles annonces Vinted des marques suivies, sous le prix max ;
2. ouvre chaque annonce et la fiche du vendeur ;
3. écarte sans notification les arnaques sûres (réplique avouée, WhatsApp, numéro de téléphone, paiement hors Vinted…) ;
4. envoie les autres sur le téléphone, via l'appli ntfy, avec une note de fiabilité et le lien.

Les notes de fiabilité :

- 🟢 75 % et plus : fiable
- 🟡 de 50 à 74 % : à vérifier
- 🔴 moins de 50 % : suspect

Le bot n'achète rien et ne se connecte à aucun compte Vinted.

## Recevoir les notifications

1. Installe l'appli **ntfy** (Play Store ou App Store).
2. Touche **+** et entre le nom du sujet secret, celui qui est rangé dans le secret `NTFY_SUJET` du dépôt.

## Modifier les recherches

1. Sur github.com, ouvre `config.json` et clique sur le crayon ✏️.
2. Modifie le fichier puis clique sur **Commit changes**.

Le passage suivant en tient compte. Les réglages disponibles :

| Réglage | Effet |
|---|---|
| `prix_max` | Prix au-dessus duquel l'annonce est ignorée. |
| `prix_marche` | Prix habituel d'une pièce authentique en occasion. Il sert à repérer les prix trop beaux. |
| `tailles` | Exemple : `["M", "L"]`. Laisse `[]` pour toutes les tailles. |
| `pointure_min_exclue` | Exemple : `40` = seulement les pointures au-dessus de 40. |
| `score_minimum` | `0` = tout sauf les arnaques sûres. `50` = seulement 🟢 et 🟡. |

## Mettre en pause ou relancer

1. Onglet **Actions**, puis **Bot Vinted**.
2. Clique sur **⋯** puis **Disable workflow** pour le mettre en pause, ou **Enable workflow** pour le relancer.

## Vérifier que tout va bien

- **Onglet Actions, workflow « Test accès Vinted »** : clique sur **Run workflow**. Le test vérifie l'accès à Vinted et envoie une notification d'essai.
- **Chaque nuit**, une notification discrète « bilan » arrive sur le téléphone.
- **Si Vinted bloque plusieurs passages de suite**, une notification « ⚠️ Bot Vinted bloqué » arrive.

## Faire tourner le bot ailleurs

Le même script tourne aussi sur un PC, un Raspberry Pi ou un vieil Android (Termux). Il faut alors :

1. remplir `ntfy_sujet` dans `config.json` ;
2. lancer `python3 vinted_bot.py`.
