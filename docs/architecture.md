# Architecture de GreenKnowledge : décisions

Journal des décisions d'architecture, tenu étape par étape. Chaque étape est
validée avant de passer à la suivante. Ce document fait foi : en cas de
divergence avec le README ou le code, c'est lui qui a raison.

Langue de travail : français. Le README reste en anglais.

## Statut des étapes

| | Étape | Statut |
|---|---|---|
| 1 | Besoins | close |
| 2 | Responsabilités | close |
| 3 | Flux | close |
| 4 | Données | à faire |
| 5 | Interfaces | à faire |
| 6 | Choix techniques | à faire |
| 7 | Qualités | à faire |
| 8 | Validation | à faire |

## Étape 1 : besoins (close)

Problème principal : gb choisit mal ses skills parmi environ 500, rédigés en
français, de qualité inégale, avec des quasi-doublons.

Problème secondaire : gb théorise au lieu de vérifier avec ses outils.

Critère de succès : gb ne doit plus dire « ah oui tu as raison, je n'ai pas le
bon skill ».

Périmètre : une instance de GK par Hermes, sur le même VPS, dans un conteneur
séparé. Hors sujet : modifier les skills, servir plusieurs agents.

Contraintes : survivre aux mises à jour de Hermes ; ne pas dupliquer les
skills ; brief court ; poser la question quand c'est ambigu ; tout par API.

### Décisions

1. « gb théorise au lieu de vérifier » est un objectif **secondaire**. Le brief
   garde ses lignes « À vérifier » et le prompt système garde sa règle, mais le
   succès de la v1 ne se mesure que sur le choix des skills. Raison : avec deux
   objectifs principaux, aucun verdict n'est possible.

2. Pas de cible chiffrée a priori. Deux phases :
   - **Phase 1** (3 semaines) : la chaîne de mesure tourne seule, sans brief
     injecté. On obtient la valeur de référence.
   - **Phase 2** : la cible est fixée à partir de ce chiffre, puis le brief est
     branché.

   Conséquence de planning : la mesure se déploie **avant** le brief. Sans
   « avant », aucune amélioration n'est démontrable.

## Étape 2 : responsabilités (close)

### Les quatre principes de frontière

1. **GK informe, gb décide.** GK ne route pas, il recommande. Le choix final du
   skill reste à gb, qui peut ignorer le brief. Conséquence : le succès ne se
   mesure pas par « GK a bien choisi » mais par la disparition des aveux.

2. **GK est greffier, pas enquêteur.** Il enregistre des faits sourcés et
   datés, les restitue, les fait vieillir. Il ne vérifie jamais un fait
   lui-même : il n'a aucun outil et ne touche pas à l'infrastructure.

3. **Hermes fait foi pour les skills, GK fait foi pour les faits.** Deux sources
   de vérité étanches. GK n'invente ni ne copie un skill ; inversement, les
   faits et les retours d'usage n'existent que chez GK.

4. **GK ne bloque jamais et ne parle jamais le premier.** Tout le trafic va de
   Hermes vers GK. GK n'a aucun canal retour et aucun accès disque. Ses seules
   connexions sortantes sont les API du LLM et des embeddings.

### Ce dont GK est responsable

| Responsabilité | Détail |
|---|---|
| Tenir le catalogue | Recevoir le manifeste, détecter les écarts par empreinte, maintenir l'index plein texte et les vecteurs |
| Choisir les skills | Recherche hybride sur le corps complet, fusion des classements, petit nombre de candidats |
| Tenir les faits | Stocker avec source et date, restituer, faire vieillir, dédoublonner |
| Décider quoi dire | Produire les lignes « Skills », « Connu », « À vérifier », « Demander » |
| Se taire | Message trivial ou confiance trop basse : pas de brief plutôt qu'un mauvais brief |
| Rédiger le brief | Court, format stable |
| Enregistrer l'usage | Skill proposé, skill utilisé, résultat |
| Mesurer | Compter les corrections et pouvoir attribuer chacune |
| Se ranger | Tâche de fond : extraction nocturne, expiration, dédoublonnage, signalement des skills jamais choisis |
| Rendre compte | `check`, `stats` |

GK ne fait **jamais** : modifier un skill, copier leur contenu, exécuter quoi
que ce soit, toucher à l'infrastructure, servir un second agent, stocker des
conversations, bloquer gb.

### Ce qui reste à Hermes et gb

Tenir la conversation. Appeler GK au bon moment et accoler le brief. Porter les
deux règles dans le prompt système. Décider et agir : retenir ou écarter les
skills proposés, poser la question, aller vérifier. Charger et exécuter les
skills. Les outils, les accès, les approbations. Le Curator garde son
périmètre : le cycle de vie des skills que l'agent crée lui-même.

### Ce qui ne relève ni de l'un ni de l'autre

Réécrire les descriptions de skills (GK signale les skills jamais choisis, la
réécriture est humaine). Dédoublonner ou supprimer des skills. Mettre à jour
Hermes, le compose, le VPS. Les décisions produit.

### Frontières tranchées

**a) Qui décide qu'il faut poser une question.** GK propose la question, gb la
pose. Pas de ligne « Demander » par principe : seulement quand deux skills
candidats mènent à des actions contradictoires (prod contre préprod, lire
contre écrire). Une ambiguïté qui ne change pas l'action ne mérite pas de
question.

**b) Détection des corrections.** Par motifs, en local, sans LLM. Les motifs
vivent dans un `motifs.yaml` séparé, dans le volume de GK, rechargé sans
redémarrage, versionné dans le dépôt comme valeurs par défaut.

On ne conserve **aucun** échantillon des réponses de gb. Conséquence acceptée :
les motifs ne sont améliorables que vers l'avant, et le chiffre de référence est
un plancher, pas une vérité.

Règle qui en découle : les motifs sont **gelés** pendant toute la mesure. GK
enregistre l'empreinte de `motifs.yaml` avec chaque période, et `gk stats`
refuse de comparer deux périodes dont les empreintes diffèrent.

Ce qui est stocké par correction : date, demande résumée, skill recommandé,
skill utilisé, extrait de l'aveu. Jamais la réponse entière.

**c) Attribution des corrections.** GK journalise chaque brief émis
(`{id, date, demande, skills recommandés}`). Une correction est donc attribuable
à l'un de deux cas, et l'indicateur est **coupé en deux dès le premier jour** :

- mauvaise recommandation (la recherche de GK est en cause) ;
- brief ignoré (le prompt système et la forme du brief sont en cause).

Sans cette coupure, on risque d'améliorer un moteur de recherche alors que le
problème est que gb ne lit pas. Bénéfice annexe : c'est le journal des briefs
qui transforme chaque correction en cas de test réutilisable.

**d) Entrée et correction des faits.** GK **propose**, l'utilisateur
**confirme**. GK ne vérifie jamais un fait (principe 2) et ne compte pas sur gb
pour l'alimenter spontanément : gb est précisément l'agent qui oublie, on ne
refait pas ce pari.

Deux rythmes, portés par l'ordonnanceur **interne à GK** (ni Hermes ni l'hôte,
pour n'ajouter aucun mode de panne) :

- chaque nuit : extraction des faits candidats, hors du chemin de service ;
- chaque semaine : présentation de la file de confirmation.

Trois règles, parce que ce mécanisme repose sur une présence humaine qui
finira par manquer :

1. **Un fait non confirmé n'entre jamais dans un brief.** Si personne ne relit,
   GK n'a aucun fait à servir ; il ne sert pas du non vérifié. La relecture est
   un bonus, pas une dépendance.
2. **La file doit se vider en deux minutes** : 5 à 10 candidats par semaine, pas
   50. Mieux vaut rater des faits que noyer la file.
3. **On n'extrait que des tours remarquables** : ceux qui sont notés, ceux où un
   aveu a été détecté, ceux où l'utilisateur a demandé de retenir quelque chose.
   Réduit d'un ordre de grandeur le coût LLM et ce qui part chez le fournisseur.

La synthèse hebdomadaire arrive sur un **canal Discord dédié**, distinct du fil
de conversation. GK y poste par **webhook**, directement : il ne demande pas à
gb de relayer, ce qui romprait le principe 4 et rendrait la synthèse dépendante
de la bonne volonté de gb. Discord reste le média de gb, mais le message ne
traverse pas son processus. Un tiers appelé en sortie ne contredit pas le
principe 4, qui interdit à GK de s'adresser à gb, pas de parler au monde.

Ce même canal porte tout le journal de GK : synthèse hebdomadaire, alarme du
canari, avertissement de catalogue périmé.

En cas de conflit entre deux faits, GK garde le plus récent et **signale** ; il
n'arbitre jamais en silence et ne tranche pas entre deux faits contemporains
contradictoires : il montre les deux avec leurs dates.

**e) Mise à jour du catalogue de skills : par push, pas par scan.** Un script
lancé par Hermes envoie le catalogue à GK ; GK met à jour ses propres entrées.
Celui qui écrit prévient, celui qui lit n'a pas à surveiller. Supprime au
passage la course de lecture d'un `SKILL.md` en cours d'écriture.

Le push porte un **manifeste complet** (`{chemin, empreinte}` pour les ~500
skills), pas des deltas. GK compare et ne réclame le corps que des skills dont
l'empreinte a bougé. Le mécanisme est ainsi **auto-réparateur** : une exécution
manquée retarde, elle ne désynchronise pas. Un manifeste gère aussi les
suppressions gratuitement, et peut porter les compteurs du Curator
(`skills/.usage.json`).

Le script est **le plugin lui-même**, qui envoie le manifeste au plus une fois
par heure, en asynchrone, sans jamais retarder un tour. Un cron séparé serait un
second mécanisme susceptible de disparaître silencieusement ; dans le plugin,
aucun nouveau mode de panne n'est créé.

Trois conséquences :

1. Le montage disque disparaît : GK n'a plus besoin de
   `/home/webmanager/hermes/data` monté en lecture seule.
2. « Pas de copie des skills » se renforce : GK n'a besoin du corps qu'au moment
   d'indexer (tokenisation FTS, vectorisation), puis il le jette. Le brief ne
   cite que des noms. À revoir à l'étape 6 si un reranker est ajouté, car lui a
   besoin du corps au moment de servir.
3. La fraîcheur du catalogue devient surveillable et doit l'être : le manifeste
   porte une date, `gk check` annonce « catalogue reçu il y a N jours ».

## Étape 3 : flux (close)

### Aucun LLM dans le chemin de service

Décision maîtresse de l'étape. Le handoff prévoyait 4 s de latence pour
DeepSeek, le README un timeout de 3 s ; les deux sont incompatibles avec un
brief injecté avant que gb commence à réfléchir.

Or aucune ligne du brief n'a besoin d'un LLM :

| Ligne | Ce qui la produit |
|---|---|
| `Skills:` | recherche hybride et compteurs d'usage, pure récupération |
| `Connu:` | faits courts déjà stockés, retrouvés par requête |
| `À vérifier:` | un fait de plus de 30 jours l'est mécaniquement |
| `Demander:` | une règle sur les métadonnées des candidats, pas un jugement |

Le LLM ne sert donc qu'en tâche de fond, pour l'extraction nocturne des faits
candidats. Ce que cette décision règle d'un coup :

- le budget passe d'environ 4 s à environ 300 ms, dominés par l'embedding de la
  requête ;
- le mode réflexion de DeepSeek sort du chemin critique : la nuit, 4 s
  n'intéressent personne ;
- plus de coût LLM par message, et plus d'envoi de chaque message au
  fournisseur ;
- le chemin de service n'a plus qu'une dépendance externe au lieu de deux.

### Flux 1 : avant le message

```
message utilisateur
  |
  +-- pre_llm_call --> plugin --MCP/HTTP--> GK.prepare(task, conv_id)
  |                                           |
  |                                           +-- trivial ? -> brief vide
  |                                           +-- FTS5 + vecteurs -> fusion
  |                                           +-- faits confirmés, datés
  |                                           +-- journalise le brief (id)
  |                                           v
  +-- brief accolé au message <-------------- brief + brief_id
  v
tour de gb
```

Le filtre des messages triviaux vit **dans GK**, pas dans le plugin : un
aller-retour sur le réseau interne coûte une milliseconde, et avoir les règles
en un seul endroit rechargeable à chaud vaut ce prix. Le plugin reste bête.
Seule exception, un message vide, qu'il n'envoie pas.

Trois niveaux de dégradation, pour tenir le « ne jamais bloquer » :

| Niveau | Condition | Résultat |
|---|---|---|
| 1 | tout va bien | brief complet, FTS et vecteurs |
| 2 | API d'embeddings lente ou hors service (> 500 ms) | brief sur FTS seul |
| 3 | GK lent ou hors service (> 1 s) | aucun brief, le message passe nu |

Le niveau 2 est le plus important : il évite qu'une panne chez le fournisseur
d'embeddings fasse disparaître le brief alors qu'un demi-brief suffirait.

### Flux 2 : pendant le message

gb peut appeler `recall`, `remember` et `rate` en cours de tour.

**Deux provenances de faits, deux traitements.** L'étape 2 interdit à un fait non
confirmé d'entrer dans un brief ; appliqué littéralement, `remember` devient
inutile. La provenance tranche :

- **`remember` appelé par gb** quand l'utilisateur vient de dire la chose : la
  source est sa parole, le fait est confirmé et immédiatement utilisable ;
- **fait extrait par GK la nuit** depuis une conversation : c'est une inférence,
  il part en file de confirmation.

Le trou de cette distinction est que gb pourrait enregistrer une inférence en la
présentant comme la parole de l'utilisateur. Il n'est pas malveillant, seulement
peu fiable. Donc chaque fait garde son origine, et la synthèse hebdomadaire
liste aussi les faits auto-confirmés : non pour les approuver, mais pour pouvoir
en attraper un mauvais. Coût nul, et l'humain reste dans la boucle sans être un
goulot.

**Un identifiant de conversation est nécessaire.** Quand l'utilisateur dit
« c'était mauvais, tu aurais dû prendre perf-audit », gb appelle `rate`, et GK
doit savoir à quel brief rattacher la note. Le plugin transmet donc un
identifiant de conversation stable à chaque appel, et GK garde les derniers
briefs par conversation. Nouvelle exigence sur le contrat du plugin, à reprendre
à l'étape 5.

### Flux 3 : après le message

Deux événements arrivent, et **leur ordre n'est pas garanti** : `post_llm_call`
apporte la réponse de gb, `on_skill_lifecycle` apporte le skill réellement
utilisé. Or classer une correction (mauvaise recommandation contre brief ignoré)
exige les deux.

Une correction détectée s'écrit donc en **attribution en attente**, et se
complète quand les événements de skill du même tour arrivent. Au bout de 60 s
elle se ferme avec ce qui est connu. Une correction non attribuable reste
comptée dans le total et sort du détail : le total ne mente jamais.

Tout ce flux est à sens unique, sans attente de réponse : le plugin envoie et
oublie.

### Flux 4 : en tâche de fond

| Rythme | Travail | Où |
|---|---|---|
| à chaque tour, au plus 1 fois par heure | envoi du manifeste du catalogue | plugin, asynchrone |
| à réception d'un manifeste | indexation et vectorisation des skills modifiés | GK |
| chaque nuit | extraction des faits candidats, tours remarquables seulement | GK, avec LLM |
| chaque semaine | synthèse sur le canal Discord | GK, webhook |
| chaque jour | purge des faits de plus de 180 jours | GK |
| en continu | canari : aucun `prepare` depuis 24 h, alerte Discord | GK |

**Le vieillissement des faits n'a besoin d'aucune tâche.** « Connu » contre
« À vérifier » est `maintenant - date < 30 jours`, calculé au moment de
construire le brief. Seule la purge dure à 180 jours demande un travail
périodique. Un rouage de moins.

### Démarrage à froid

Un GK qui redémarre avec une base vide n'a aucun catalogue, et ne peut pas en
réclamer un sans parler le premier.

**GK signale ses besoins dans les réponses qu'il envoie déjà.** Son `prepare`
renvoie un drapeau « catalogue vide », le plugin le voit et envoie le manifeste
immédiatement, hors throttle. Un redémarrage est rattrapé en un message au lieu
d'une heure, et le principe 4 tient : répondre « au fait, il me manque ceci »
n'est pas une initiative.

C'est le mécanisme général de toute demande de GK vers le plugin.

## Interface entre le plugin et GK

**MCP over HTTP**, pour tous les appels : `prepare`, `report`, le manifeste du
catalogue, `remember`, `recall` et `rate`. Un seul mécanisme à maintenir plutôt
que deux.

Deux risques connus et acceptés, qu'un tuyau HTTP/JSON simple aurait supprimés :

- `plugins.entries.gk-bridge.mcp_allowlist` reste nécessaire dans le
  `config.yaml` de Hermes : une ligne de plus qu'une migration peut jeter. Déjà
  couverte par `gk check`.
- Le format de retour de `ctx.call_mcp` est inconnu. Le plugin doit accepter
  défensivement les formats plausibles, comme le faisait le prototype, jusqu'à
  vérification contre un vrai Hermes.

## Mesure du succès

Deux instruments, de natures différentes et complémentaires.

| Instrument | Rôle |
|---|---|
| Comptage automatique par motifs | le volume et la tendance, sur des centaines de messages |
| Notation humaine | la vérité terrain, quelques dizaines de points fiables |

Le comptage automatique a un angle mort : il repose sur gb qui **avoue**. Un gb
devenu moins enclin à reconnaître ses erreurs ressemblerait à un progrès. La
notation humaine y échappe, puisqu'elle ne passe pas par gb.

Les deux se confrontent : sur une même période, comparer les sessions notées
mauvaises et les sessions où les motifs ont trouvé un aveu. Une divergence
franche signifie que les motifs sont faux. `gk stats` affiche ce taux d'accord.

**Règle** : l'indicateur principal ne dépend jamais des notations. Elles
calibrent, elles ne mesurent pas. Si personne ne note pendant un mois, le
système continue de fonctionner et perd seulement sa calibration.

La notation n'est pas un sous-système : c'est l'outil existant
`report(skill, outcome, note)` appelé par un humain au lieu d'un hook. Deux
portes vers le même point :

- `gk rate bad --skill perf-audit` en CLI, fiable et sans couplage à Hermes ;
- un outil `rate` exposé à gb, pour pouvoir le dire en langage naturel. La
  dépendance à gb est ici acceptable, l'instruction étant explicite dans le
  message.

Contrainte : utilisable en une seconde, et fonctionnelle même tapée nue.

## Résilience aux mises à jour de Hermes

Deux familles de pannes, presque toutes silencieuses.

**Les fichiers disparaissent** : `plugins/` hors du volume persistant,
`config.yaml` réécrit par une migration qui jette `mcp_allowlist`, plugin remis
à désactivé.

**L'API change** : hook renommé ou supprimé, texte retourné qui n'est plus
accolé au message utilisateur (le pire : GK est appelé, le brief part, personne
ne le lit), format de manifeste de plugin modifié.

Protections, par ordre d'efficacité :

1. **Épingler le tag de l'image** dans le compose de gb. Transforme « ça casse un
   jour au hasard » en « ça casse le jour où je l'ai décidé ».
2. Le plugin dans le volume persistant, vérifié comme tel.
3. `gk check` lancé automatiquement : au démarrage de GK, et dans la procédure
   de mise à jour, qui devient un rituel en trois temps (pull, recréation,
   `gk check`).
4. **Canari passif** : GK sait qu'il devrait être appelé à chaque message. Le
   silence est l'alarme. Aucune supervision externe à installer.
5. Réduire la surface d'API touchée par le plugin.
6. **Le plugin ne lève jamais d'exception à l'import.** Tout en `try/except`, et
   en cas d'échec il ne fait rien. À vérifier dans les docs : un plugin qui
   plante au chargement empêche-t-il gb de démarrer ?

## Points ouverts

| Sujet | État |
|---|---|
| Chemins réels dans le conteneur de gb | Non vérifié. `~/.hermes` est le défaut documenté, mais `HERMES_HOME=/opt/data` le surcharge. À lever par `echo $HERMES_HOME; ls -la /opt/data` |
| `docker-compose.yml` de gb | Non récupéré. Dira si l'image est épinglée et quels volumes sont montés |
| URL des docs Hermes | `hermes-agent.nousresearch.com` ne résout pas. Bloque la vérification de la stabilité des hooks et du comportement d'un plugin qui plante |
| Code du prototype | Absent du dépôt. Les 45 tests et les 86 % de Hit@1 du handoff ne sont pas vérifiables ici |
| Écriture GitHub | L'app Claude a la lecture seule sur le dépôt. `git push` et l'API renvoient 403. À accorder depuis https://github.com/apps/claude/installations/select_target |
| Licence | À choisir avant publication. Le fichier a été supprimé au commit 3b94d02 |
| Fournisseur d'embeddings | À trancher à l'étape 6. DeepSeek n'en propose pas |
| Tokenizer FTS5 pour le français | `porter` applique des règles anglaises. À traiter à l'étape 6 |
