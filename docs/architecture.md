# Architecture de GreenKnowledge : décisions

Journal des décisions d'architecture, tenu étape par étape. Chaque étape est
validée avant de passer à la suivante. Ce document fait foi : en cas de
divergence avec le README ou le code, c'est lui qui a raison.

Langue de travail : français. Le README reste en anglais.

**Deux numérotations, qui ne se recouvrent pas.** Les **étapes de conception**
1 à 8 listées ici sont des décisions. Les **phases de construction** 1 à 4
(1 chaîne de mesure, 2 index et banc, 3 brief et A/B, 4 faits sourcés) sont du
code livré. Une décision prise à l'étape de conception 5 peut ne se réaliser
qu'en phase de construction 3 : dire « étape » seul pour les deux est une source
de confusion, et ce document ne le fait pas.

## Statut des étapes

| | Étape de conception | Statut |
|---|---|---|
| 1 | Besoins | close |
| 2 | Responsabilités | close |
| 3 | Flux | close |
| 4 | Données | close |
| 5 | Interfaces | close |
| 6 | Choix techniques | close |
| 7 | Qualités | close |
| 8 | Validation | close |

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

2. Pas de cible chiffrée a priori. La chaîne de mesure se déploie **avant** le
   brief, de sorte qu'aucune amélioration ne soit revendiquée sans point de
   comparaison.

   **Révisé à l'étape 8** : la comparaison se fait par A/B sur la même période
   (le brief injecté un tour sur deux) et non plus par deux phases successives,
   dont la seconde aurait été confondue par tout ce qui bouge en sept semaines.
   Le déploiement de la mesure avant le brief reste acquis, et sert désormais
   aussi à constituer le corpus du banc d'essai.

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
   connexions sortantes sont l'API du LLM, la nuit, et le webhook Discord. Le
   chemin de service, lui, ne sort pas du tout (étape 4).

### Ce dont GK est responsable

| Responsabilité | Détail |
|---|---|
| Tenir le catalogue | Recevoir le manifeste, détecter les écarts par empreinte, maintenir l'index plein texte |
| Choisir les skills | Recherche plein texte sur le corps complet, petit nombre de candidats |
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

1. Le montage disque disparaît : GK n'a plus besoin du volume de données de
   Hermes monté en lecture seule. (Ce volume est `./data` relatif au compose,
   monté sur `/opt/data` dans le conteneur, et non le
   `/home/webmanager/hermes/data` qu'annonçait le handoff. Les skills y sont en
   `/opt/data/skills`, vérifié.)
2. « Pas de copie des skills » se renforce : GK n'a besoin du corps qu'au moment
   d'indexer (tokenisation FTS), puis il le jette. Le brief ne cite que des
   noms. À revoir à l'étape 6 si un reranker est ajouté, car lui a besoin du
   corps au moment de servir.
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
| `Skills:` | recherche plein texte et compteurs d'usage, pure récupération |
| `Connu:` | faits courts déjà stockés, retrouvés par requête |
| `À vérifier:` | un fait de plus de 30 jours l'est mécaniquement |
| `Demander:` | une règle sur les métadonnées des candidats, pas un jugement |

Le LLM ne sert donc qu'en tâche de fond, pour l'extraction nocturne des faits
candidats. Ce que cette décision règle d'un coup :

- le mode réflexion de DeepSeek sort du chemin critique : la nuit, 4 s
  n'intéressent personne ;
- plus de coût LLM par message, et plus d'envoi de chaque message au
  fournisseur ;
- le chemin de service perd une de ses deux dépendances externes. L'étape 4 lui
  retirera la seconde, voir « Pas d'embeddings en v1 ».

### Flux 1 : avant le message

```
message utilisateur
  |
  +-- pre_llm_call --> plugin --MCP/HTTP--> GK.prepare(task, conv_id)
  |                                           |
  |                                           +-- trivial ? -> brief vide
  |                                           +-- FTS5 sur le corps complet
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
| 1 | tout va bien | brief complet |
| 2 | GK lent ou hors service (> 1 s) | aucun brief, le message passe nu |

Il y avait un niveau intermédiaire, qui repliait sur FTS seul quand l'API
d'embeddings faiblissait. L'étape 4 l'a rendu sans objet : FTS **est** le chemin,
et il est local. Ce niveau reviendra si les embeddings sont ajoutés en v2.

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
| à réception d'un manifeste | indexation des skills modifiés | GK |
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

## Étape 4 : données (close)

### Inventaire

| Donnée | Propriétaire | Source de vérité | Rétention | Reconstructible |
|---|---|---|---|---|
| Catalogue de skills (chemin, empreinte) | GK | **Hermes** | tant que présent au manifeste | oui, gratuitement |
| Index FTS5 (`content=''`) | GK | dérivé | idem | oui, gratuitement |
| Corps des SKILL.md | — | — | **jamais stocké** | — |
| Vecteurs des skills | — | — | **aucun en v1** | — |
| Faits confirmés | **GK** | **GK** | 180 jours puis purge | **non** |
| Faits candidats en attente | GK | GK | 30 jours puis abandon | non |
| Historique des faits remplacés | GK | GK | avec le fait courant | non |
| Journal des briefs | GK | GK | **7 jours** | non |
| Corrections | **GK** | **GK** | trois niveaux, voir plus bas | non |
| Notations | **GK** | **GK** | sans limite | non |
| Événements d'usage de skills | GK | GK | détail 30 jours, puis compteurs | non |
| Compteurs du Curator | Hermes | Hermes | reçus au manifeste | oui |
| Échantillon des réponses de gb | — | — | **néant** | — |

### Quatre lignes seulement sont irremplaçables

Les faits, les corrections, les notations et les compteurs d'usage. Tout le reste
est dérivé d'un manifeste ou jeté par construction. Et ces quatre lignes pèsent
des dizaines de kilo-octets, pas des méga-octets.

La question des sauvegardes se réduit donc à **exporter quatre tables** :
`gk export` produit un JSON de quelques dizaines de ko, et c'est à l'ops de
sauvegarder le volume.

Taille de la base complète, sans vecteurs : un index FTS sans contenu et
quelques centaines de ko de journaux. **Moins d'un méga-octet.** La limite de
512 Mo du conteneur est très largement suffisante.

### Journal des briefs : 7 jours

À quoi il sert vraiment : attribuer une correction, ce qui se joue en 60
secondes, et fabriquer un cas de test, ce que la fiche de correction fait en
recopiant ce qu'il lui faut au moment de l'attribution.

Il n'a donc besoin d'aucune longue conservation. **7 jours**, pour déboguer
(« pourquoi ce brief-là ? »), pas pour archiver. Comme il contient les messages
de l'utilisateur, la différence entre 7 et 90 jours n'est pas un détail.

### Corrections : une durée de validité, pas une durée de vie

Le volume n'est pas un argument : 20 corrections par jour à 400 octets font 3 Mo
par an. Mais deux choses évoluent en sens inverse. **L'utilité décroît** : une
correction portant sur un skill réécrit depuis est un fossile, et elle pollue la
statistique en mélangeant deux états de la bibliothèque. **L'exposition ne
décroît pas** : l'extrait et la demande tronquée restent aussi sensibles au bout
de deux ans.

La bonne règle n'est donc pas temporelle. **Une correction vaut tant que les
skills qu'elle nomme gardent la même empreinte.** GK l'applique mécaniquement,
puisque le manifeste lui apporte les empreintes chaque heure.

| Niveau | Contenu | Durée | Texte utilisateur |
|---|---|---|---|
| **Le compteur** | date, classification, skill recommandé, skill utilisé, empreinte de `motifs.yaml` | **sans limite** | non |
| **Le cas de test** | demande tronquée, skill attendu | tant que les empreintes tiennent | oui |
| **La preuve** | extrait de l'aveu, 200 caractères | **90 jours** | oui |

Le compteur est exactement ce qu'il faut pour comparer la phase 1 à la phase 2 ;
ne contenant aucun texte, le garder indéfiniment ne coûte et n'expose rien. Le
texte brut expire une fois qu'il a servi.

L'extrait sert à vérifier qu'un motif a attrapé un vrai aveu plutôt qu'un faux
positif. C'est un audit du détecteur, qui se fait dans les jours qui suivent :
90 jours sont généreux.

On garde des lignes individuelles plutôt que des agrégats : sous ce schéma elles
ne contiennent plus de texte, et elles permettent de redécouper par skill ou par
semaine plus tard.

**`gk forget`**, en trois formes, que ce découpage rend possibles :

- `--before <date>` purge les niveaux 2 et 3, garde les compteurs ;
- `--conv <id>` purge tout ce qui vient d'une conversation ;
- `--extracts` efface toutes les preuves, garde compteurs et cas de test.

### Pas d'embeddings en v1

Décision majeure de l'étape, qui retire une brique que le handoff tenait pour
acquise.

**Le raisonnement.** Le handoff annonce 86 % de premier choix correct avec le
corps complet, et déclare par ailleurs que les vrais embeddings n'ont jamais
tourné (Hugging Face bloqué, testés contre un faux serveur). Donc **ces 86 % ont
été obtenus sans eux**, par FTS seul sur le corps complet. Et SkillRouter ne dit
pas qu'il faut des vecteurs : il dit que le corps du skill est le signal
décisif, *toutes méthodes de récupération confondues*.

**Ce que la décision supprime** : l'envoi de la bibliothèque entière à un tiers
à l'indexation (500 documents décrivant l'infrastructure et les procédures
internes, soit la divulgation la plus lourde du projet) ; l'envoi du texte de
chaque message à chaque tour ; le choix d'un fournisseur ; le coût ; le stockage
des vecteurs ; et le niveau de dégradation intermédiaire de l'étape 3.

Le chemin de service devient **entièrement local à GK, sans aucune dépendance
externe**, et tombe à environ 20 ms.

**Le caveat, assumé** : les 86 % portent sur 23 skills factices. Sur 500 skills
français réels avec des quasi-doublons, ça tiendra moins bien, et c'est là que
les synonymes manquants font mal. Ce n'est donc pas une certitude mais une
hypothèse à mesurer, d'où l'ordre imposé à l'étape 6 :

1. corriger le tokenizer français, qui devient la **première** priorité
   technique puisqu'il conditionne tout le reste ;
2. mesurer FTS seul sur les **vrais** skills avec le banc d'essai ;
3. n'ajouter les embeddings que si le chiffre l'exige — la décision se prendra
   alors avec un gain mesuré en face d'une exposition connue, au lieu d'être
   supposée d'avance.

**L'auto-hébergement du modèle est écarté pour la v1**, mais pas pour les
raisons avancées d'abord. La décision tient sur la simplicité et sur l'exigence
de mesurer avant d'ajouter, pas sur le matériel.

L'argument matériel initial était que le VPS expose un modèle de processeur QEMU
générique, probablement sans AVX2, ce qui aurait coûté un facteur 3 à 10 sur
l'inférence. **Vérification faite, c'était faux** : `avx` et `avx2` sont tous
deux présents. Et le compose limite Hermes à `cpus: "2.0"` sur 4, donc deux vCPU
entiers restent hors de sa portée. Avec AVX2, 60 Go de disque libéré et 6,2 Go de
RAM disponible, **un modèle d'embeddings local est faisable** et mérite d'être
évalué sérieusement en v2 si la mesure réclame des embeddings.

### Un conflit de compteurs, tranché

Deux comptages d'usage des skills vont coexister : ceux du **Curator**
(`skills/.usage.json`, reçus au manifeste) et ceux de **GK** (ses propres
événements, rattachés à un `brief_id`). Ils divergeront.

**Ceux de GK font foi pour le brief**, parce qu'ils répondent à la bonne
question : « est-ce que *ma* recommandation a marché ? », là où le Curator compte
tous les usages, recommandés ou non. Ceux du Curator restent du contexte
informatif, utiles pour repérer un skill populaire que GK ne recommande jamais.

**On ne tente pas de les réconcilier.** Deux compteurs qui mesurent deux choses
différentes, nommés distinctement, valent mieux qu'un chiffre bâtard.

### Ce qui sort de GK, et vers qui

| Destinataire | Ce qu'il reçoit | Fréquence |
|---|---|---|
| LLM (DeepSeek) | les tours remarquables uniquement | la nuit |
| Discord | la synthèse : faits, candidats, alertes | chaque semaine |

C'est tout, après la décision « pas d'embeddings en v1 ». Rien ne sort sur le
chemin de service.

Le handoff signalait comme point de vigilance que « chaque message et les faits
associés partent chez DeepSeek ». Après l'étape 3, ce n'était plus DeepSeek mais
le fournisseur d'embeddings qui voyait passer chaque message — le risque avait
changé de destinataire sans diminuer. La décision de l'étape 4 le supprime.

L'URL du webhook Discord est elle-même un secret : quiconque la détient peut
poster sur le canal.

### Les secrets

Jeton d'accès au serveur GK, clé du LLM, URL du webhook. Ils appartiennent à
l'ops, vivent en variables d'environnement ou en secrets du compose, et **ne
sont ni dans la base ni dans le dépôt**. Le `config.example.yaml` ne porte que
des valeurs factices.

### Une correction de vocabulaire

« Demande résumée » est faux : résumer demanderait un LLM, et il n'y en a pas
dans ce chemin. C'est une **troncature**. Le terme est corrigé partout pour ne
pas laisser croire à une capacité qui n'existe pas.

## Étape 5 : interfaces (close)

### Les cinq interfaces, et qui les contrôle

| Interface | Sens | Contrôle |
|---|---|---|
| Plugin vers GK | MCP over HTTP | **nous, entièrement** |
| GK vers plugin | champ `besoins` dans les réponses | **nous, entièrement** |
| gb vers GK | MCP, 3 outils | **nous, entièrement** |
| GK vers Discord | webhook, un seul sens | nous, mais le format est à Discord |
| Plugin et Hermes | API de plugin | **pas nous** |

Les quatre premières se conçoivent pour durer. La cinquième ne se conçoit pas,
elle se subit, et c'est là que tout le risque se concentre.

### Le transport : MCP over HTTP

Pour tous les appels, dans les deux points d'entrée. Un seul mécanisme à
maintenir plutôt que deux. Deux risques connus et acceptés, qu'un tuyau
HTTP/JSON simple aurait supprimés :

- `plugins.entries.gk-bridge.mcp_allowlist` reste nécessaire dans le
  `config.yaml` de Hermes : une ligne de plus qu'une migration peut jeter. Déjà
  couverte par `gk check`.
- Le format de retour de `ctx.call_mcp` est inconnu. Le plugin doit accepter
  défensivement les formats plausibles, comme le faisait le prototype, jusqu'à
  vérification contre un vrai Hermes.

### La règle qui gouverne tout : le plugin ne comprend pas le brief

Le plugin vit dans le volume de Hermes ; le mettre à jour veut dire toucher à
gb, donc à la production. Il faut donc que **GK puisse évoluer sans que le
plugin bouge jamais.**

D'où la règle : **le brief est un texte opaque pour le plugin.** Il ne le lit
pas, ne connaît pas la ligne `Skills:`, ne sait pas qu'il existe des faits. Il
reçoit une chaîne et l'accole au message. C'est un coursier, pas un lecteur.

Tout changement de format du brief est donc gratuit : ajouter une ligne, en
retirer une, changer l'ordre, sans rien redéployer chez gb.

Par extension, **GK sert aussi la section de prompt système**, les deux règles
qui disent à gb d'obéir au brief. Codées dans le plugin, les affiner demanderait
un redéploiement. Le plugin les demande à GK au chargement et les enregistre ; il
garde une version minimale en dur au cas où GK serait absent à ce moment-là.

### Deux points d'entrée, pas un

Si gb et le plugin parlent au même serveur MCP, gb voit **tous** les outils, y
compris `prepare`, qu'il n'a aucune raison d'appeler, et `catalog`, qu'il
pourrait appeler avec n'importe quoi.

| Point d'entrée | Outils | Qui l'appelle |
|---|---|---|
| `/mcp/bridge` | `prepare`, `observe`, `catalog` | le plugin seul, jeton propre |
| `/mcp/agent` | `recall`, `remember`, `report` | gb, déclaré dans sa config |

Cela préserve l'intention du README : la surface d'outils est délibérément
petite, pour que GK ne recrée pas le problème qu'il doit résoudre. **gb voit
trois outils, pas six.**

### Les contrats

```
prepare  -> { v, conv_id, task }
         <- { v, brief, brief_id, besoins[] }

observe  -> { v, conv_id, brief_id, reply }
         <- { v, besoins[] }

catalog  -> { v, skills: [{path, fingerprint, usage?}], complete: true }
         <- { v, wanted: [path], besoins[] }
catalog  -> { v, bodies: [{path, body}] }
         <- { v, besoins[] }

report   -> { v, conv_id?, brief_id?, skill, outcome, note?, source }
remember -> { v, fact, source }   <- { v, status }
recall   -> { v, query }          <- { v, facts: [{fact, source, date, status}] }
```

Trois détails qui portent des décisions antérieures.

**Le manifeste se fait en deux temps.** GK répond au manifeste par la liste des
corps qu'il veut, et le plugin ne renvoie que ceux-là. Sans quoi 500 corps
traverseraient le réseau toutes les heures pour rien.

**`observe` transmet la réponse entière, GK n'en garde qu'un extrait.** Le texte
complet traverse le fil, il n'est pas retenu. C'est ce qui rend l'étape 4
tenable.

**`report` porte un champ `source` : `hook`, `agent` ou `human`.** L'étape 4 a
décidé que les compteurs de GK font foi et que les notations humaines servent de
calibration ; les confondre dans un seul compteur détruirait la confrontation
qui rend la mesure vérifiable. Il n'y a donc pas d'outil `rate` séparé : une
notation est un `report` avec `source: human`.

### Le canal retour : `besoins`

Chaque réponse porte une liste `besoins`, le mécanisme de l'étape 3 pour que GK
demande sans parler le premier. Une seule valeur aujourd'hui, `catalogue_vide`.

Règle qui le rend durable : **un besoin que le plugin ne connaît pas est ignoré
en silence.** On peut donc en ajouter autant qu'on veut sans toucher au plugin.

### Comment ça évolue sans casser

**Une règle de tolérance, et c'est presque tout.** Dans les deux sens : **un
champ inconnu est ignoré, un champ absent prend sa valeur par défaut.** Cela ne
coûte rien à écrire et rend tous les ajouts gratuits, dans les deux directions,
pour toujours.

**On ne versionne que les ruptures.** Un ajout ne change pas `v`, la règle de
tolérance le couvre. Seul un changement incompatible l'incrémente, et alors GK
sert les deux versions pendant une fenêtre de transition : `/mcp/bridge/v1` et
`/mcp/bridge/v2` coexistent le temps que le plugin soit remplacé.

**Un test de contrat dans les deux sens.** Le prototype avait un faux plugin
appelant le vrai serveur. Il manque l'autre moitié : un faux serveur répondant
au vrai plugin, y compris avec des champs et des besoins inconnus, pour prouver
que la tolérance fonctionne au lieu d'y croire.

### L'interface qu'on ne contrôle pas

Le plugin touche six points de l'API de Hermes : `register_hook`,
`register_system_prompt_section`, `call_mcp`, et les hooks `pre_llm_call`,
`on_skill_lifecycle`, `post_llm_call`. Chacun peut disparaître ou changer de
forme à une mise à jour, et aucune règle de tolérance n'y changera rien : si
`pre_llm_call` est renommé, le hook ne tourne plus.

Il n'y a donc pas de compatibilité à concevoir ici, mais deux choses :

**Détecter vite** — `gk check` vérifie les six points, le canari passif alerte
sur le silence. Décidé aux étapes 2 et 3.

**Rendre le remplacement bon marché** — c'est la vraie assurance, et elle change
la façon d'écrire le plugin. S'il fait cinquante lignes, n'utilise que la
bibliothèque standard, ne garde aucun état et ne comprend rien à ce qu'il
transporte, le réécrire pour une nouvelle API de Hermes est une heure de
travail, pas un chantier. C'est pour cela que toute l'intelligence doit être
dans GK et aucune dans le plugin.

Autrement dit : pour les interfaces qu'on maîtrise, on conçoit la
**compatibilité**. Pour celle qu'on ne maîtrise pas, on conçoit la
**remplaçabilité**.

## Étape 6 : choix techniques (close)

Les étapes précédentes ont vidé cette étape d'une bonne part de son contenu. Il
ne reste qu'un vrai sujet, la recherche en français, et des choix qui s'imposent
d'eux-mêmes.

### Langage et dépendances

**Python 3.11+.** Le plugin doit être en Python puisqu'il tourne dans Hermes,
FTS5 est dans le `sqlite3` de la bibliothèque standard, et une seule langue
permet aux tests de contrat de partager du code.

| Dépendance | Pourquoi | Évitable |
|---|---|---|
| une bibliothèque serveur MCP | implémenter le transport à la main serait absurde | non |
| `snowballstemmer` | stemming français, Python pur, sans compilation | non |
| `PyYAML` | `config.yaml` et `motifs.yaml` | oui, en passant au JSON |
| `sqlite3`, `urllib`, `threading` | stockage, HTTP sortant, ordonnanceur | déjà dans la stdlib |

Pas d'APScheduler : un fil et une boucle suffisent pour deux tâches périodiques.
Pas de client HTTP tiers : `urllib` suffit pour deux appels sortants. Pas d'ORM :
quelques tables, du SQL écrit à la main. C'est volontaire — chaque dépendance
est une dette de mise à jour sur un serveur censé tourner sans surveillance.

Sur la bibliothèque MCP, une réserve : le handoff dit « FastMCP 4 », mais l'état
et la maintenance des bibliothèques MCP n'ont pas pu être vérifiés depuis
l'environnement de conception. Prendre celle qui est officiellement maintenue,
**épingler la version exacte**, et garder la couche MCP mince — même logique de
remplaçabilité que pour le plugin.

**Le plugin : stdlib stricte.** Ce n'est pas une préférence mais une contrainte,
puisqu'on ne peut rien installer dans l'image de Hermes.

### Stockage

**SQLite, un seul fichier, dans le volume dédié.** Postgres serait un second
conteneur pour un écrivain unique et quelques méga-octets ; un fichier s'exporte
et se sauvegarde trivialement, ce que demande le `gk export` de l'étape 4.

**Mode WAL**, et ce n'est pas un détail : GK lit sur le chemin de service pendant
qu'il écrit (ingestion de manifeste, extraction nocturne). Sans WAL, une écriture
bloque les lectures, donc l'extraction nocturne pourrait figer un `prepare`.
Avec les 20 ms visées, cela ruinerait le budget.

**FTS5 avec `content=''`**, cohérent avec « on ne stocke jamais le corps ».
Conséquence : reconstruire l'index exige de redemander les corps. Le mécanisme
`besoins` le couvre — `catalogue_vide` se généralise en `index_a_reconstruire`.

Pas d'ORM.

### La recherche

FTS5 est désormais le seul moteur. Trois problèmes distincts.

**a) Le français.** Les options de tokenizer :

| Tokenizer | Comportement | Verdict |
|---|---|---|
| `porter` | stemming anglais | celui du prototype, et faux |
| `unicode61` | découpe et replie les accents, pas de stemming | base correcte |
| `trigram` | tout sous-mot de 3 caractères | index énorme, BM25 perd son sens |
| sur mesure | extension C | trop lourd |

**Décision : `unicode61 remove_diacritics 2`, et le stemming français fait en
Python.** GK stemme les mots à l'indexation et à la requête avec
`snowballstemmer`, et indexe la forme stemmée. FTS5 reste vanille, toute la
logique française est dans du Python qu'on contrôle et qu'on peut régler, et
`snowballstemmer` est du Python pur sans compilation — ce qui compte sur un VPS
sans AVX2. Le stemming français reste imparfait : c'est le banc d'essai qui le
dira, pas une discussion.

**b) Le découpage.** Un `SKILL.md` de 400 lignes indexé en un seul document
dilue BM25 : les documents longs sont pénalisés et une correspondance dans un
recoin lointain ne veut rien dire. **Découper par titre markdown**, indexer
chaque morceau, et le score du skill est celui de son **meilleur morceau**,
auquel s'ajoutent les champs de tête. C'est ce qui permet d'indexer le corps
complet, comme le recommande SkillRouter, sans que la longueur devienne un
handicap.

**c) Le classement.** BM25, fourni par FTS5, avec des **poids par champ** (nom,
description, corps). Aucune valeur n'est fixée ici : SkillRouter dit que le corps
est le signal décisif, donc le réflexe de surpondérer le nom serait une erreur.
Ce sont des paramètres à régler au banc d'essai, pas à deviner.

### Le modèle

**DeepSeek, la nuit uniquement**, API compatible OpenAI, pour la seule
extraction des faits candidats.

**Inversion de critère** : la latence étant devenue gratuite, le travail nocturne
prend le **meilleur** modèle disponible, pas le plus rapide. Le mode réflexion
qui inquiétait le handoff devient un avantage. Le bouton `extra_body` reste dans
la config, sans urgence.

Sortie en JSON avec un exemple dans le prompt — DeepSeek le recommande et
renvoie parfois du vide sans.

**Le travail nocturne est idempotent et reprenable** : s'il meurt à mi-course, le
relancer ne duplique pas les faits candidats. Clé d'idempotence, l'identifiant du
tour.

### Déploiement

| | |
|---|---|
| Forme | service ajouté au `docker-compose.yml` de gb |
| Réseau | **boucle locale**, `http://127.0.0.1:8765/mcp` — voir la note ci-dessous |
| Image de base | `python:3.11-slim`, tag épinglé |
| Utilisateur | non-root |
| Volumes | un volume dédié pour la base, aucun montage des données de Hermes |
| Mémoire | 256 Mo, à confirmer par la mesure |
| Healthcheck | `/health`, que `gk check` interroge aussi |

Les 512 Mo du handoff supposaient des vecteurs et peut-être un modèle local.
Sans eux, GK c'est Python, SQLite et un petit index, vraisemblablement 80 à
150 Mo. Le chiffre de 256 Mo sera **mesuré après déploiement, pas deviné**.

**Le réseau : pas de bridge possible.** Le compose de gb tourne en
`network_mode: host`, donc il n'existe aucun réseau bridge et la résolution par
nom de service (`http://gk:8765`) est impossible. GK est donc sur un réseau
bridge à lui, **publié sur la boucle locale seule** :
`ports: ["127.0.0.1:8765:8765"]`, et le plugin vise `http://127.0.0.1:8765`.
GK garde ainsi son propre espace réseau — il ne voit pas les autres services de
l'hôte — tandis que Hermes l'atteint par la boucle locale. L'alternative (GK
aussi en `network_mode: host`) a été écartée : plus simple, mais GK verrait
toute la pile de l'hôte.

Le `deploy/` du prototype, qui cible systemd et pipx, est à jeter.

### Ce que la conception ne peut pas produire

La décision « pas d'embeddings en v1 » repose sur une mesure qui n'existe pas
encore : FTS seul tient-il sur 500 skills français réels ? Les 86 % du handoff
portaient sur 23 skills factices.

Il faut pour cela **20 à 30 tâches réelles accompagnées du skill qui aurait dû
être choisi** : de vraies demandes, formulées comme l'utilisateur les écrit, avec
la bonne réponse. C'est la seule pièce du projet qui ne peut être ni déduite ni
fabriquée, et c'est elle qui valide ou invalide le choix le plus structurant.

Les corrections détectées en produiront automatiquement par la suite, mais il
faut un premier jeu pour démarrer.

## Étape 7 : qualités (close)

Beaucoup avait déjà été tranché en chemin. Cette étape rassemble, et comble cinq
trous trouvés en relisant l'ensemble.

### Latence

| Étape du chemin de service | Coût |
|---|---|
| aller-retour HTTP sur le réseau interne | ~1 ms |
| stemming de la requête | ~1 ms |
| requête FTS5 sur ~500 skills découpés | quelques ms |
| requête des faits | ~1 ms |
| assemblage du brief | négligeable |

**Trou 1 : le journal des briefs est une écriture sur le chemin critique.** La
décision (c) de l'étape 2 fait journaliser chaque brief, ce qui n'avait pas été
vu comme une écriture synchrone. Avec WAL c'est rapide, mais un `fsync` au
mauvais moment fait un pic.

Correctif : `PRAGMA synchronous=NORMAL` avec WAL, et journalisation **en file
d'attente** vidée par le fil de fond. Ce qu'on perd sur une coupure de courant,
ce sont les dernières lignes d'un journal de débogage à 7 jours : sans
importance. **Les faits et les corrections restent en écriture synchrone.**

Le travail nocturne tourne dans le même processus mais il est **lié aux
entrées-sorties** (appels API) : il libère le GIL et ne concurrence pas le chemin
de service pour le CPU, et avec WAL sa connexion écrit sans bloquer les lectures.

### Pannes

**Trou 2 : les attributions en attente ne survivent pas à un redémarrage.**
L'étape 3 écrit une correction en « attribution en attente », fermée à 60 s. Si
GK meurt dans l'intervalle, la ligne reste en attente indéfiniment et sort des
statistiques sans que personne le sache. Correctif : une **réconciliation au
démarrage** qui ferme toute attribution de plus de 60 secondes avec ce qui est
connu.

**Trou 3 : l'intégrité n'était pas traitée.** `PRAGMA integrity_check` au
démarrage, et une histoire de reprise qui découle de l'étape 4 :

| Donnée | Si la base est perdue |
|---|---|
| Catalogue et index | se reconstruit seul, via `besoins` |
| Journal des briefs | perte sans conséquence |
| Faits, corrections, notations, compteurs | **ne se reconstruisent pas**, il faut la sauvegarde `gk export` |

L'ingestion du manifeste se fait **en une seule transaction**, tout ou rien. Un
plantage à mi-course n'applique rien, et comme le manifeste est auto-réparateur,
l'heure suivante remet tout d'aplomb.

**Trou 4, le plus sérieux : le canari ne peut pas se surveiller lui-même.** Le
canari détecte « le plugin a cessé de m'appeler », mais il vit dans GK. Un GK
mort ne peut pas alerter sur sa propre mort.

- `restart: unless-stopped`, et un **healthcheck qui teste vraiment** :
  `/health` exécute une requête FTS triviale et renvoie l'âge du catalogue, au
  lieu de répondre 200 sans rien vérifier.
- Et surtout : **la synthèse hebdomadaire part même quand il n'y a rien à dire.**
  « Rien à signaler cette semaine » est un message utile, parce que c'est son
  **absence** qui devient le signal. Un battement de cœur qui ne bat que
  lorsqu'il y a des nouvelles n'est pas un battement de cœur, et c'est la seule
  façon de détecter depuis l'extérieur un GK entièrement mort.

### Sécurité

Le modèle de menace réel : personne n'attaque ce service. Les risques véritables
sont un port publié par erreur, gb qui fait quelque chose de maladroit, et l'URL
du webhook qui fuite. La réponse est proportionnée à cela.

**La frontière est le binding sur la boucle locale** plus le pare-feu de
l'hôte. Ce n'est pas un réseau docker interne, puisque gb tourne en
`network_mode: host` : c'est un cran moins fort, et il faut le savoir.

**Deux jetons distincts**, un par point d'entrée, mais ils ne protègent presque
rien et il vaut mieux le dire. Les deux finiront dans la configuration de
Hermes, lisible dans son conteneur ; et surtout **le compose monte le socket
Docker de l'hôte dans gb** (`/run/user/1001/docker.sock`, rootless, uid 1001),
donc gb peut arrêter le conteneur GK, y entrer et lire son environnement. Face à
cela aucun jeton ne tient. La séparation des jetons est un **garde-fou contre la
maladresse**, pas une frontière de sécurité.

**La file de confirmation des faits est aussi un contrôle de sécurité.** Un fait
de GK est injecté dans le prompt de gb, et les faits candidats sont extraits de
conversations par un LLM : du contenu de conversation peut donc devenir un fait.
La confirmation humaine de l'étape 4 est ce qui ferme ce chemin. Elle n'était là
que pour la qualité ; c'est une raison de plus de ne jamais servir un fait non
confirmé.

Le brief est une donnée pour gb, pas une instruction d'exécution. L'URL du
webhook est un secret, puisqu'elle suffit à poster sur le canal.

### Mises à jour de Hermes

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
4. **Canari passif**, complété par le battement hebdomadaire ci-dessus.
5. Réduire la surface d'API touchée par le plugin.
6. **Le plugin ne lève jamais d'exception à l'import.** Tout en `try/except`, et
   en cas d'échec il ne fait rien. À vérifier dans les docs : un plugin qui
   plante au chargement empêche-t-il gb de démarrer ?

### Mises à jour de GK

**Trou 5 : les migrations de schéma n'avaient jamais été évoquées.** Une table
`schema_version` et des migrations ordonnées appliquées au démarrage. Banal,
mais sans cela la première évolution de schéma corrompt la base.

Et une règle qui en découle, puisqu'il n'y a ni pré-production ni seconde
instance : **les migrations sont additives uniquement.** On ajoute des colonnes
et des tables, on n'en supprime ni n'en renomme jamais. Revenir au tag précédent
reste donc possible, parce que l'ancienne version sait encore lire la base. Sans
cette règle le retour arrière est impossible et chaque mise à jour devient un
pari.

Une mise à jour de GK ne doit jamais exiger une mise à jour du plugin : la règle
de tolérance de l'étape 5 le garantit.

### Supervision

**Un seul endroit à regarder : le canal Discord.**

| Ce qui y arrive | Quand |
|---|---|
| Synthèse hebdomadaire | chaque semaine, **même vide** |
| Alarme du canari | aucun `prepare` depuis 24 h |
| Catalogue périmé | manifeste trop ancien |
| Échec répété du travail nocturne | après plusieurs nuits |

**Règle anti-bruit** : GK ne parle sur Discord que pour ce qui demande une action
humaine, plus le battement hebdomadaire. Tout le reste va dans les logs, sur la
sortie standard, ramassés par Docker. Sinon le canal est mis en silencieux au
bout de trois semaines et les alertes ne servent plus à rien, exactement comme
les notations.

À la demande : `gk check` pour l'état, `gk stats` pour la mesure.

### Un principe transverse

**GK se dégrade, il n'échoue pas.** Quand il ne peut pas construire un brief, il
renvoie un brief vide avec un code 200, jamais une erreur 500. Le « fail open »
du plugin devient alors trivial à écrire, et un plugin trivial est un plugin qui
ne casse pas.

## Étape 8 : validation (close)

### Trois preuves, qui n'ont pas la même valeur

| Preuve | Ce qu'elle établit | Confondants |
|---|---|---|
| **Banc d'essai** : corps complet contre métadonnées seules | GK choisit mieux que gb ne le fait seul | **aucun** |
| **A/B en production** | le système change le comportement de gb | peu |
| **Avant/après** | rien de solide à lui seul | beaucoup |

La première est la preuve propre, et le prototype la faisait déjà sans le dire :
68 % avec les métadonnées seules, 86 % avec le corps complet. Or **les
métadonnées seules sont exactement ce que voit gb aujourd'hui** — nom et
description courte. Cet écart *est* donc la mesure de ce que GK apporte, établie
hors production, sans aucun confondant.

### L'A/B remplace l'avant/après

Révision de la décision 2 de l'étape 1. La mesure en deux phases était
méthodologiquement faible : sur sept semaines, la bibliothèque de skills, la
nature du travail, le modèle de gb et la façon même de formuler les demandes
peuvent tous avoir changé. Une baisse de moitié ne prouverait rien.

**GK tire à pile ou face à chaque tour et n'injecte le brief qu'une fois sur
deux**, en notant le tirage dans le journal des briefs.

| | Avant/après | A/B |
|---|---|---|
| Comparaison | deux périodes différentes | **la même période** |
| Bibliothèque, charge, modèle | peuvent avoir changé | **identiques par construction** |
| Jeu de motifs | à geler par discipline | **identique par construction** |
| Bénéfice pendant l'essai | nul puis entier | la moitié |
| Délai avant une réponse fiable | 3 semaines puis 4 | les deux bras s'accumulent en parallèle |

Le coût est réel : la moitié des tours sans brief pendant l'essai. Ce qu'on
achète est une comparaison fiable, et plus vite. C'est devenu facile grâce au
journal des briefs et à la classification des corrections, qui n'existaient pas
quand l'étape 1 a été tranchée.

### Le critère d'acceptation de la v1

**Sur de vraies tâches et les vrais skills, de combien le corps complet bat-il
les métadonnées seules ?**

Aucun seuil n'est fixé d'avance, et c'est volontaire : un chiffre rond décidé
avant la mesure serait une invention. Le seuil se fixe **devant le résultat** —
un écart de 20 points justifie le système à lui seul, un écart de 3 points ne
vaut pas son conteneur et impose de rouvrir la question des embeddings.

S'y ajoutent les conditions de forme : les tests passent, les tests de contrat
passent **dans les deux sens**, et `gk check` est vert contre un vrai Hermes.

### Le corpus du banc se construit tout seul

L'étape 1 déploie la chaîne de mesure **avant** le brief. Or cette chaîne produit
exactement la donnée du banc, étiquetée gratuitement :

| Type de cas | Origine | Qui l'étiquette |
|---|---|---|
| **Cas difficiles** | les corrections détectées | l'aveu de gb, qui nomme le skill qui aurait convenu |
| **Cas faciles** | un tour sur vingt où un skill a servi sans correction ensuite | le skill utilisé, puisque personne ne s'est plaint |

Zéro travail manuel dans les deux cas : le jeu de tâches est un **sous-produit de
la phase de mesure, pas un prérequis**.

Une seule addition : une table `bench_candidates` qui échantillonne les cas
faciles, puisque le journal des briefs ne vit que 7 jours. Elle ne garde que la
demande tronquée et le skill utilisé, rien de plus que ce que les corrections
stockent déjà.

**Caveat** : un banc fait uniquement de corrections est un banc de cas
difficiles. C'est informatif, puisque c'est là qu'est la valeur, mais son score
absolu n'est pas comparable aux 86 % du prototype. D'où l'échantillonnage des
cas faciles en contrepoids.

Une alternative avait été envisagée — rédiger 20 à 30 tâches à la main depuis
d'anciennes conversations — et écartée pour son coût. Elle portait un biais
sérieux qu'il vaut la peine de noter pour mémoire : **rédiger les tâches avec la
liste des skills sous les yeux conduit à employer leur vocabulaire**, et le banc
affiche alors un score flatteur qui ne tient pas en production. Le corpus issu de
la production n'a pas ce défaut, par construction.

### Le banc n'est pas bloquant

Les deux mesures répondent à des questions différentes :

- **l'A/B décide « on garde ou pas »** : GK change-t-il le comportement de gb ;
- **le banc décide « FTS ou embeddings »** : pourquoi cela ne marche pas, le cas
  échéant.

Si l'A/B est bon, le banc n'est jamais nécessaire. Il ne sort de sa boîte que si
le résultat déçoit, pour diagnostiquer.

### Le contrôle de cohérence qui tient l'ensemble

Le banc dit « GK choisit bien ». Les corrections classées disent « en production,
les erreurs viennent de la recommandation ou de l'obéissance ». Les deux doivent
s'accorder :

- banc bon et corrections attribuées à « mauvaise recommandation » → **le banc
  ment**, ses tâches ne ressemblent pas à la réalité ;
- banc bon et corrections attribuées à « brief ignoré » → la recherche va bien,
  le problème est dans le prompt système ou la forme du brief.

Sans la coupure en deux de l'étape 2, ce diagnostic serait impossible.

### Ce qui tourne en continu

| Quoi | Où | Quand |
|---|---|---|
| Tests unitaires et de contrat | CI GitHub Actions | à chaque poussée |
| Banc sur les fixtures | CI | à chaque poussée, garde-fou de régression |
| Banc sur les **vrais** skills | en local | avant une mise en production, score consigné |
| `gk check` | au démarrage et au rituel de mise à jour | toujours |
| Canari et battement hebdomadaire | GK vers Discord | en continu |

**Distinction assumée** : les vrais skills ne peuvent pas entrer dans un dépôt
public, puisqu'ils décrivent l'infrastructure de GREENMUSICIANS. La CI ne garde
donc que le banc sur fixtures ; le banc réel est un rituel local dont seul le
score est consigné dans un `bench/history.jsonl` versionné. On perd
l'automatisme, on garde la trace de la dérive.

### Une barrière avant d'écrire du code

Trois points ouverts ne sont pas des détails de mise en œuvre : ce sont des
hypothèses dont dépendent des décisions déjà prises.

| Hypothèse | Ce qui s'écroule si elle est fausse |
|---|---|
| Les chemins réels dans le conteneur (`/opt/data`) | le déploiement, `gk check`, l'emplacement du plugin |
| Le format de retour de `ctx.call_mcp` | le plugin, qui accepte trois formats « plausibles » |
| Un plugin qui plante bloque-t-il le démarrage de gb ? | la gravité de toute panne du plugin |

Les trois se résolvent en quelques minutes avec un accès au VPS, et se paient en
jours si l'on construit par-dessus une mauvaise réponse. **Elles doivent être
closes avant la première ligne de code**, pas pendant.

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

La notation n'est pas un sous-système : c'est l'outil `report` appelé avec
`source: human` au lieu de `source: hook` (étape 5). Deux portes vers le même
point :

- `gk rate bad --skill perf-audit` en CLI, fiable et sans couplage à Hermes ;
- `report` exposé à gb sur `/mcp/agent`, pour pouvoir le dire en langage
  naturel. La dépendance à gb est ici acceptable, l'instruction étant explicite
  dans le message.

Contrainte : utilisable en une seconde, et fonctionnelle même tapée nue.

## L'environnement, vérifié

Mesuré sur le déploiement réel, en remplacement des valeurs du handoff.

| Élément | Valeur vérifiée |
|---|---|
| Image | `nousresearch/hermes-agent:v2026.9.21`, **épinglée**, `pull_policy: missing` |
| Réseau | `network_mode: host` — **aucun réseau bridge disponible** |
| Ressources de gb | `cpus: "2.0"` sur 4 vCPU de l'hôte, `memory: 6G`, `shm_size: 1gb` |
| Volume persistant | `./data` à côté du compose, monté sur `/opt/data`, **4,7 Go** |
| `HERMES_HOME` | `/opt/data`, via `env_file: .env` |
| `HOME` du processus Hermes | `/opt/data` (défini dans la commande du compose ; un `exec` voit `/root`) |
| Skills | **`/opt/data/skills`**, **537 SKILL.md** — la prémisse du handoff (« environ 500 ») est confirmée |
| Plugins | **`/opt/data/plugins/`** |
| Config de Hermes | **`/opt/data/config.yaml`** — c'est là que va `mcp_allowlist` |
| `~/.hermes` | `/opt/data/.hermes` : contient `cron/`, `scripts/`, `state/` et deux `.conf` métier. Ni skills ni plugins, et ce n'est pas le dossier de configuration principal |
| Ordonnanceur de Hermes | `.hermes/cron` existe et sert déjà. L'ordonnanceur interne à GK reste le choix retenu, mais c'est un arbitrage et non une nécessité |
| Profils | `/opt/data/profiles/`, `profiles-archives/`, `bascule-detonantes-*/` : ce déploiement a **plusieurs profils d'agent** |
| Socket Docker | `/run/user/1001/docker.sock` monté dans gb : **rootless confirmé**, et gb a la main sur Docker |
| CPU | **AVX et AVX2 présents** |
| Secrets | `/opt/data/.env`, 29 Ko, plus trois sauvegardes contenant aussi des secrets |
| API Hermes | serveur HTTP sur `${API_SERVER_PORT:-8742}` avec un `/health` |
| Déjà en service | MCP (`.mcp-discovery.lock`), Discord (identifiant dans une sauvegarde `.env`), Curator (`.curator_backups`) |
| Convention de plugin | `/opt/data/plugins/<nom>/` avec `plugin.yaml` (clés `name`, `version`, `description`, `author`, `kind`, `hooks`) et `__init__.py`. **On désactive en renommant le manifeste** en `plugin.yaml.inactif`, pas par une commande |
| Plugins en place | `gm-routing` (actif), `gm-provenance`, `gm-discord-cards`, `gm-destructive-guard`, `gm-routing-socle-test`, et `gm-skill-dup-guard` **inactif** |

### Les hooks, vérifiés dans la source de Hermes

Les trois points d'accroche de la conception existent sur `v2026.9.21`, avec la
sémantique supposée. `hermes_cli/plugins.py` les liste parmi les hooks de plugin
valides, aux côtés de `transform_llm_output`.

| Fait | Source |
|---|---|
| `pre_llm_call` renvoie une **chaîne**, **ajoutée au message utilisateur** | `agent/turn_context.py:453` : `plugin_user_context: str = ""  # pre_llm_call context (appended to user message)` |
| Les contributions de **plusieurs plugins sont collectées**, pas écrasées | `_collect_pre_llm_call_context` : « Run `pre_llm_call` plugins » |
| Un hook qui lève est **attrapé et journalisé**, pas fatal | `turn_context.py:783` : `logger.warning("pre_llm_call hook failed: %s", exc)` |
| `post_llm_call` est **une fois par tour, après la boucle d'outils** | `agent/turn_finalizer.py:416` |

Deux conséquences. La règle « le brief est un texte opaque pour le plugin »
(étape 5) tombe exactement sur le mécanisme réel. Et la coexistence avec
`gm-routing`, qui s'accroche à `pre_verify` et `pre_tool_call`, ne pose aucun
problème : même sur un hook partagé, les contextes s'additionnent.

**Les charges utiles, relevées dans `hermes_cli/hooks.py`.**

```python
"pre_llm_call":  {session_id, user_message, conversation_history,
                  is_first_turn, model, platform}
"post_llm_call": {session_id, model, platform}
```

Deux conséquences, l'une heureuse et l'autre non.

**`session_id` est fourni.** L'étape 3 inscrivait « il faut un identifiant de
conversation » comme une nouvelle exigence sur le contrat du plugin : elle est
déjà satisfaite. Et `is_first_turn` permet de pousser le manifeste au premier
tour d'une session plutôt que sur une minuterie.

**`post_llm_call` ne porte pas le texte de la réponse**, donc le détecteur de
corrections y serait aveugle. Le hook à utiliser est **`transform_llm_output`**,
que `VALID_HOOKS` décrit comme « return a replacement string (first non-None
wins) or None » : pour transformer une sortie il faut la recevoir, et
`turn_finalizer.py:416` confirme qu'il tire avant `post_llm_call` sur le même
tour. Le détecteur s'y accroche et **renvoie toujours `None`**, ce qui le rend
strictement sans effet puisque seul le premier retour non nul compte. Un
observateur pur sur un hook de transformation. Il est en revanche sur le chemin
de la réponse, donc l'envoi-sans-attente de l'étape 3 devient impératif.

**Le modèle de plugin, relevé dans `gm-routing`.** On déclare les hooks dans
`plugin.yaml` et on implémente des **fonctions de module** `on_<hook>(**kwargs)`.
Il n'y a **ni objet `ctx` ni `register_hook`** : le modèle du handoff était faux.

```python
def on_pre_tool_call(**kwargs):
    session_id = str(kwargs.get("session_id") or "")
```

Le pont devient donc trois fonctions nues : `on_pre_llm_call` qui renvoie le
brief, `on_transform_llm_output` qui renvoie `None` et observe, et
`on_skill_lifecycle`.

**`call_mcp`, signature relevée** (`hermes_cli/plugins.py:513`) :

```python
def call_mcp(self, server: str, tool: str,
             arguments: Optional[Dict[str, Any]] = None,
             timeout: float = 30) -> Dict[str, Any]:
    ...
    return self._mcp_envelope(raw)
```

| Fait | Conséquence |
|---|---|
| Le retour est un **dictionnaire**, produit par `_mcp_envelope` | fin des « trois formats plausibles » du prototype |
| **Défaut-refus** : `PermissionError` si le serveur n'est pas dans `plugins.entries.<plugin_id>.mcp_allowlist` | confirme l'étape 5 mot pour mot |
| `timeout` **borné à 1–600 s**, défaut 30 | le seuil de dégradation à 1 s de l'étape 3 n'est pas un choix mais le **plancher imposé** par Hermes |
| Synchrone, via le client natif, avec disjoncteur et reconnexion | le « fail open » s'appuie sur le disjoncteur au lieu de le contourner |
| Résultats **tronqués au-delà de 64 Ko** (`_MCP_RESULT_CHAR_CAP`) | borne ce que GK renvoie : brief et liste `wanted` passent, mais jamais de corps de skills dans ce sens |

`call_mcp` étant une méthode portée par un objet d'API par plugin — qui offre
aussi `has_capability` et l'enregistrement d'outils —, **il existe bien un objet
de contexte** ; ce sont les *hooks* qui ne le reçoivent pas, puisqu'ils
reçoivent des kwargs. Comment un plugin obtient cet objet est la dernière
inconnue du pont. Le choix « MCP over HTTP » de l'étape 5 est donc viable :
c'était le risque sérieux, il est écarté.

**Sur `on_skill_lifecycle`, le risque s'atténue mais déplace une décision.** `plugins.py:128` le décrit
comme portant les « successful skill lifecycle facts », ce qui laissait craindre
qu'il ne rapporte que les réussites — or l'étape 3 s'en sert pour savoir quel
skill a réellement servi, y compris quand le tour a mal tourné.

L'appel réel, dans `tools/skill_usage.py` :

```python
invoke_hook("on_skill_lifecycle", action=action, skill_name=skill_name,
            provenance=..., task_id=..., session_id=...,
            use_count=..., reused=..., reuse_after_patch=...)
```

La docstring dit « lifecycle hook **after an authoritative state change** » et
`_mutate_and_emit` précise « **only if the write landed** » : le « successful »
du commentaire qualifiait l'écriture, pas le skill. Aucune inquiétude de ce
côté.

**Mais ce hook ne dit pas ce que la conception attendait.** Il signale qu'un
*enregistrement* de skill a changé, avec son `use_count` — c'est la télémétrie
du Curator. Il ne dit pas « ce skill a servi à ce tour et ça a marché ».

Le compteur de réussites du brief se construit donc de **deux sources** : les
usages depuis ce hook (`skill_name`, `session_id`, `task_id`), et les échecs
depuis les corrections détectées et les notations humaines. Ce qui donne
« utilisé 14 fois, 2 corrections » plutôt qu'un drapeau de réussite
auto-déclaré — mieux fondé, et cohérent avec la décision de l'étape 4 selon
laquelle les compteurs de GK font foi parce qu'ils répondent à la bonne
question.

**La prétendue incohérence des chemins n'existait pas.** Le handoff voyait une
contradiction entre `HERMES_HOME=/opt/data` et l'affirmation de gb que
`~/.hermes` vaut `/opt/data/.hermes`. Les deux sont vrais et compatibles, puisque
`HOME` vaut `/opt/data`. Seule l'inférence du handoff — « donc `~` vaut
`/opt/data/home` » — était fausse : ce dossier n'existe pas. Le handoff avait
raison sur l'emplacement des skills, gb avait raison sur `.hermes`.

**La bibliothèque est dupliquée hors de sa racine.** 866 `SKILL.md` existent dans
le volume pour une bibliothèque de 537 :

| Chemin | SKILL.md | Nature |
|---|---|---|
| `/opt/data/skills` | **537** | la bibliothèque vivante |
| `/opt/data/profiles` | 137 | les skills d'un profil |
| `/opt/data/bascule-detonantes-20261015` | 137 | un instantané du précédent |
| `/opt/data/home` | 32 | existe, mais n'est pas le `HOME` du processus |
| `/opt/data/homr` | 20 | une faute de frappe devenue dossier |
| `/opt/data/.venv` | 3 | embarqués dans un paquet |

Le handoff soupçonnait « probablement des quasi-doublons » : en voilà la preuve
matérielle. Deux conséquences de conception :

- **Une seule racine d'indexation, nommée explicitement** dans la configuration :
  `/opt/data/skills`, jamais une découverte récursive. Un manifeste construit
  trop large indexerait les doublons et produirait des recommandations ambiguës,
  c'est-à-dire précisément le défaut que GK doit corriger.
- **GK signale les orphelins** : il compte les `SKILL.md` hors de la racine
  indexée et le mentionne dans la synthèse hebdomadaire. Cela relève de la
  responsabilité « se ranger » — signaler sans toucher — et personne ne le
  remarque autrement.

**Le volume n'est pas une installation propre** : scripts ad hoc à la racine
(`_add.sh`, `_corr.py`, `_fd.py`, `_inv.py`), `__pycache__`, fichiers d'état
divers, 97 entrées. Conséquence : `gk check` ne doit pas supposer un volume
pristine, et le plugin doit vivre dans un sous-dossier clairement nommé.

## La forme réelle des skills, et ce qu'elle impose

> **PÉRIMÉ — relevé du 2026-10-22, matin.** La bibliothèque est en cours de
> consolidation : 534 skills le matin, 239 à midi, 185 dans l'après-midi, et des
> fusions en cours. Tous les comptages de cette section (534 skills, 105 au-delà
> de 32 Ko, 326 avec `references/`, 537 et 866 `SKILL.md` dans le volume) sont
> donc des instantanés d'un état révolu. À refaire avec
> `tools/mesurer-rubriques.py --attendu <le compte de find>` quand la
> consolidation sera terminée. Ce qui ne dépend pas du dénominateur, en
> revanche, tient : **aucun fichier sur 239 ne portait les six rubriques**, le
> vocabulaire des titres est une longue traîne (1724 titres distincts pour 2345
> occurrences), et **la bibliothèque est bilingue** — `when to use` y est plus
> fréquent que `Déclencheurs`, ce qui invalide le stemmer français de
> l'étape de conception 6.

### Relevé du 2026-10-22, fin de journée : la structure, par GM

Fourni par l'équipe après consolidation, sur **177 skills, dont 122 avec
`references/`**. Le compte continue de bouger ; la structure, elle, est stable
et c'est elle qui décide de la phase 2.

```
<catégorie>/<nom-du-skill>/
  SKILL.md            obligatoire, ~2 à 46 Ko (médiane ~13-20 Ko)
  references/*.md     1 à 46 fichiers, chargés à la demande
  scripts/            optionnel
  templates/          optionnel
```

Frontmatter : **toujours deux clés**, `name` (= nom du dossier, préfixe `gm-`
pour le métier) et `description` (une phrase : quoi + déclencheur).

Ordre canonique du corps : `Déclencheurs`, `Règle / RÈGLE ABSOLUE` (décision
validée, **datée et attribuée**), `Contexte / Architecture / Identifiants`,
`Procédure / Workflow`, `Pièges` (`🔴 Piège n°N — ...`, un piège = un titre
autoportant), `Vérification`, `Fichiers liés / Où lire quoi` (table de routage
vers `references/`), `Historique des cas vécus`.

**Deux profils, et c'est le fait décisif :**

| Profil | Taille | Forme |
|---|---|---|
| Gros skill métier | 20-46 Ko | `SKILL.md` = sommaire + règles dures + pointeurs vers 10-46 `references/` |
| Skill outil générique (hérité Claude) | 6-17 Ko | `Overview / Quick Reference / Procedure / Pitfalls / Verification`, en anglais |

### Cinq conséquences, dont trois corrigent une étape close

**1. `Historique des cas vécus` est un corpus de banc qui existe déjà.**
L'étape 8 faisait venir le corpus des corrections de la phase 1 : il est vide,
et la consolidation l'aurait de toute façon mis au rebut par
`retire_testcases`. Or ces sections sont un journal d'incidents réels, écrits
par des humains, **avec le bon skill connu par construction** — celui qui
contient la section. À mesurer en premier : portent-elles la demande d'origine
ou seulement la résolution ? Si oui, le banc démarre sans attendre des mois
d'accumulation.

**2. Le préfixe `gm-` sépare deux populations, pas deux styles.** Les deux
profils sont deux problèmes de recherche différents. Un score moyen sur les
deux ne voudrait rien dire. Le préfixe est un classificateur gratuit, exact et
déjà présent : le banc rend deux chiffres, et la pondération peut différer.

**3. Le brief doit citer le fichier de référence, pas seulement le skill**
— correction de l'étape de conception 5. Pour un gros skill métier, `SKILL.md`
est un sommaire : s'arrêter au nom du skill laisse l'agent devant 46 fichiers.
La table `Fichiers liés / Où lire quoi` est déjà dans `SKILL.md`, donc déjà
indexée : le brief passe de « va voir par là » à « lis ceci » pour zéro
coût. Et cela **confirme** de ne pas indexer `references/` en v1 : pour choisir
*quel* skill, le sommaire est le meilleur document possible.

**4. `RÈGLE ABSOLUE` datée et attribuée est l'amorce de la phase 4.** Le magasin
de faits sourcés devait partir de rien. La bibliothèque contient déjà des
décisions datées et signées dans une forme reconnaissable (« Hubert
19/08/2026 »). L'étape 2 prévoyait de montrer deux règles contradictoires avec
leurs dates sans savoir d'où viendraient les dates : les voilà.

**5. Le découpage descend au niveau 3** — correction de l'étape de conception 6,
qui ne considérait que les titres de niveau 2. `🔴 Piège n°N` est un titre
autoportant : c'est l'unité de récupération idéale pour une demande formulée en
symptôme, soit exactement ce que `SYMPTOM_PATTERNS` de `skills_gate.py` attrape
aujourd'hui sans savoir quoi en faire.

### Une demande au gabarit `gm-skill-template`

GM propose d'écrire ce gabarit pour standardiser les nouveaux skills. Une seule
demande de la part de GK : **séparer les deux moitiés des déclencheurs en deux
titres**, par exemple `## Déclencheurs` et `## Ne pas charger si`. Le « quand NE
PAS charger » est aujourd'hui noyé dans la prose et se devine par reniflage de
négations — 36 % de détection, et rien qui puisse se tester. Avec deux titres,
la colonne à poids négatif devient une lecture structurelle.

Limite à ne pas perdre de vue : un gabarit standardise les nouveaux skills, pas
les 177 existants. Si l'index dépend d'une convention que seuls les nouveaux
respectent, GK marchera bien sur les nouveaux et mal sur les anciens, ce qui se
lira comme un défaut de GK. L'index tolère donc les deux formes et **mesure la
couverture par convention** ; `tools/mesurer-rubriques.py` rend déjà les titres
non classés par poids pour cela.

Relevé le 2026-10-22 sur les 534 skills de `/opt/data/skills`.

| Mesure | Valeur |
|---|---|
| Disposition | 30 % `SKILL.md + references/ + scripts/`, 28 % `SKILL.md` seul, 25 % `+ references/`, le reste mixte |
| Taille d'un `SKILL.md` | médiane **15 Ko**, moyenne 20, max 84 ; **105 skills dépassent 32 Ko** |
| Frontmatter | `name` et `description` **partout** ; `version` 156, `platforms` 140, `metadata` 128, `category` 112 |

Squelette type, et c'est lui qui porte les conséquences :

```
---
name: domaine-sujet
description: "<déclencheur concret> — <ce que le skill contient>"
---
## Déclencheurs      quand charger / quand NE PAS charger
## Règle             la règle métier, à l'impératif
## Procédure         commandes exactes, chemins, ordre
## Pièges            ce qui ressemble à X mais est Y
## Vérification      preuve mesurable
## Références        references/*.md datés, scripts/
```

Quatre familles en usage : procédural métier, incident et forensics, outillage
et API, campagne récurrente. Les titres varient donc d'une famille à l'autre, et
le découpage doit le tolérer.

### Quatre conséquences pour la phase 2

**1. `Déclencheurs` reçoit le poids le plus élevé**, pas le nom ni la
description. Les skills déclarent eux-mêmes quand les charger : c'est le signal
le plus fort possible, écrit par l'auteur, et structuré. La conception traitait
le corps comme un bloc homogène, ce qui était faux — une section a précisément
pour propos la question que GK pose.

**2. Le « quand NE PAS charger » est un signal NÉGATIF**, et aucune des huit
étapes ne l'avait envisagé. Un skill dont les contre-indications correspondent à
la demande doit être **écarté**, pas remonté. BM25 ne sait faire que de
l'affinité positive, donc cette section se découpe à part et se **soustrait**.
Sans cela, un skill qui dit « ne me charge pas pour les commandes fournisseurs »
remonte en tête sur « commande fournisseur », avec les mots au meilleur endroit
possible — un classement inversé sur les cas les plus nets.

**3. Le découpage par titre est une nécessité, pas une optimisation.** Une
médiane de 15 Ko fait environ 4 000 jetons : indexé d'un bloc, un skill noie
BM25. Le squelette fournit de vraies frontières sémantiques.

**4. `references/` n'est pas indexé en v1.** 326 skills en ont ; ce sont des
annexes datées qui multiplieraient le corpus sans décider du choix, puisque
`Déclencheurs` décide. À rouvrir si le banc montre des manques.

### Deux effets sur les décisions antérieures

**Le bras « métadonnées seules » du banc est exactement reproductible**, puisque
`name` et `description` sont présents partout. La comparaison de l'étape 8 n'a
aucune ambiguïté de définition.

**Le pari « sans embeddings » sort renforcé.** Le squelette impose
`description: "<déclencheur concret> — ..."`, donc les auteurs écrivent déjà les
mots que les utilisateurs emploient plutôt qu'un résumé abstrait. C'est la
condition pour que le lexical suffise. Renforcé, pas prouvé.

### Un écart non résolu

Le comptage donne **534** ici et **537** par `find -name SKILL.md` dans
`/opt/data/skills`. Trois dossiers qui n'en sont probablement pas. Sans
conséquence, noté plutôt que corrigé en silence.

Et les **105 skills de plus de 32 Ko** sont des candidats au `skill-cleaner` déjà
présent dans la bibliothèque. GK est bien placé pour les signaler, comme il
signale les orphelins.

## skills_gate.py : ce qu'il couvre, et ce qu'il laisse

Relevé le 2026-10-22 : `/opt/data/hooks/skills_gate.py`, 350 lignes, hook shell
sur `pre_llm_call`, en production. Il implémente une partie de ce que la
conception prévoyait pour la phase 3 — et il fallait le lire en entier avant de
conclure quoi que ce soit.

### Ce qu'il fait

Contrat : `{session_id, user_message, is_first_turn, model, platform}` sur
stdin, `{"context": "..."}` sur stdout, ajouté **à la fin du message
utilisateur** et jamais dans le prompt système, pour ne pas invalider le cache
de prompt. Contrainte réelle que la conception ignorait.

| Décision de conception | Déjà fait par le gate |
|---|---|
| Injection automatique avant que l'agent réfléchisse | oui |
| Brief accolé au message utilisateur | oui, et jamais dans le prompt système |
| Ignorer les messages triviaux | oui : `EXEMPT_PATTERNS`, politesses et questions d'état mesurable |
| Ne jamais bloquer l'agent | oui : toute exception donne une sortie vide et `exit 0` |
| Rappeler les skills pertinents | oui, pour **douze domaines** |

Il déduplique par session via `~/.hermes/hooks_state`, et rattrape deux cas que
les mots-clés métier ne capturent pas : `META_PATTERNS` pour les demandes qui
parlent *des* skills ou de la méthode, `SYMPTOM_PATTERNS` pour « rien ne se
passe » sans domaine nommé.

### Le chiffre décisif

La table `DOMAINS` couvre **12 domaines** et nomme **environ 36 skills**, en
chaînes écrites à la main. Sur 534 skills, **environ 498 — 93 % de la
bibliothèque — ne sont jamais recommandés**.

> **Chiffre à corriger.** La consolidation du 2026-10-22 a ramené la
> bibliothèque à 185 skills en fin de journée, et elle continue. Avec ~36 skills
> nommés par le gate, le non-couvert passe de 93 % à **environ 80 %**.
> L'argument tient, il est moins spectaculaire, et c'est le bon chiffre qui doit
> figurer ici dès que la bibliothèque ne bouge plus. Une consolidation qui
> continuerait jusqu'à quelques dizaines de skills remettrait la justification
> de GK en question, et il faudra le dire à ce moment-là plutôt que de laisser
> le chiffre vieillir.

Le gate n'est donc pas une solution au problème de GK : c'en est une aux douze
domaines les plus chauds, faite à la main et avec soin, qui laisse le reste sans
aucun mécanisme. **GK ne le remplace pas, il couvre ce qu'il ne couvre pas.**

### Trois fragilités que le code avoue

**Le premier domaine qui matche gagne**, et un commentaire l'assume : « AVANT
prestashop-catalogue : une commande fournisseur mentionne presque toujours un
produit ». C'est une collision réglée à la main par un ordre de priorité. À
douze domaines c'est tenable ; à vingt-cinq, l'ordre devient combinatoire et
chaque ajout peut casser un arbitrage antérieur sans qu'aucun test le dise.
BM25 **score** au lieu de choisir un casier, donc il n'a pas ce problème.

**Les noms de skills sont des chaînes.** Un skill renommé ou supprimé laisse un
nom mort dans la table, et rien ne le vérifie.

**Il ne mesure rien.** Aucun comptage, aucune trace de ce que l'injection
produit.

### La division du travail retenue

GK lit la même table `DOMAINS` et **se tait quand un de ces douze domaines
matche**.

| Demande | Qui répond |
|---|---|
| Un des 12 domaines | `skills_gate.py`, sa précision réglée à la main |
| Tout le reste | GK, sur les ~498 skills orphelins |
| Dans les deux cas | GK mesure |

Aucun conflit, aucune duplication, et la précision artisanale préservée là où
elle existe. Si le banc montre plus tard que GK égale le gate sur ces douze
domaines, le gate pourra être retiré — avec un chiffre pour le justifier.

### Deux gains avant même la phase 2

**Auditer la table du gate.** GK a le catalogue réel via le manifeste : il peut
signaler les noms cités qui n'existent plus, et les ~498 skills que personne ne
nomme. Code trivial, service que rien ne rend aujourd'hui.

**Un A/B sans confondant.** Le mener sur les domaines **non couverts
uniquement** : une population où il n'existe aucune intervention. Non pas « GK
contre le gate » mais « GK contre rien », sur 93 % des demandes.

### À reprendre plutôt qu'à réécrire

`ACTION_PATTERNS` et `EXEMPT_PATTERNS` sont le filtre des messages triviaux de
l'étape 3, déjà écrits en français, avec normalisation des accents, éprouvés en
production. GK les reprend. `META_PATTERNS` et `SYMPTOM_PATTERNS` couvrent deux
trous que la conception n'avait pas vus.

## Revirement du 2026-10-22 : la phase 2 allégée

**Consigne donnée par GM** : « notre bibliothécaire a seulement besoin de nom +
description ». Elle renverse trois étapes closes. **L'index demeure — c'est le
but de GK — mais il ne porte plus que le nom et la description : plus de corps
indexé, plus de découpage par rubrique, plus de BM25 à régler, plus de colonne
à poids négatif, plus de banc de recherche à constituer.**

La section ci-dessous garde la trace d'une surinterprétation : la consigne a
d'abord été lue comme un abandon de la phase 2, ce qui aurait changé le but de
GK sans que personne le demande. Le raisonnement fautif et sa rectification sont
conservés, parce que l'erreur portait sur l'argument central du projet.

### L'objection, et pourquoi elle porte

GM : « notre bibliothécaire a seulement besoin de nom + description ».

L'objection est juste, et elle va plus loin que son énoncé. 177 skills × (un nom
+ une phrase) ≈ 25 Ko, soit ~7 000 jetons : cela tient dans un prompt. Donc pas
d'index, pas de recherche, pas de corpus de banc à constituer. La prémisse
fondatrice — « 537 skills, 10 Mo, impossible à mettre en contexte » — est vraie
du **corps** des skills et fausse de leurs **métadonnées**. La consolidation l'a
rendue plus fausse encore. L'étape 8 avait défini un bras « métadonnées seules »
comme point de comparaison à battre, sans envisager qu'il puisse être la réponse.

Elle résout au passage la décision rouverte sur le prompt système : un catalogue
de 177 lignes est **stable**, donc mis en cache une fois. C'est le contenu qui
change à chaque tour — un résultat de recherche — qui coûte le cache de prompt.
L'approche la plus simple est aussi la plus économe.

### Le relevé qui change la prémisse, arrivé dans le même échange

`/opt/hermes/hermes_cli/cli_info_mixin.py:1018` :

```
# Same shape as the system prompt's skill catalog (``    - name: description``).
```

**Hermes construit déjà un catalogue `nom : description` dans le prompt
système.** L'approche n'est donc pas à essayer : elle est en production depuis
le début. Et `skills_gate.py` a été écrit quand même — 350 lignes à la main pour
réinjecter des noms de skills par mots-clés, par-dessus un prompt système qui
les liste tous. On n'écrit pas cela si le catalogue suffit. C'est une
présomption forte, pas une preuve : le gate a peut-être été écrit pour forcer le
*chargement* plutôt que pour informer le *choix*.

### Quatre conséquences

**1. GK ne peut pas « injecter le catalogue »** : ce serait le répéter. Ce que
les descriptions portent est acquis, et n'est pas le levier.

**2. L'A/B de l'étape 8 avait un témoin trop faible.** Il était décrit comme
« GK contre rien » sur les domaines non couverts par le gate. Il n'y a pas de
« rien » : le témoin inclut le catalogue complet. Toute contribution de GK doit
battre ce catalogue, pas le vide. La barre est plus haute que ce que le document
annonçait.

**3. La question utile se déplace** vers ce qu'une phrase de description ne peut
pas porter : la désambiguïsation entre deux skills voisins (la collision
« commande fournisseur » / « commande client » que le gate règle à la main par
un ordre de priorité), le « quand NE PAS charger », et **quel fichier de
`references/` lire** parmi 46.

**4. La phase 1 est déjà l'instrument de la décision.** `on_skill_lifecycle`
enregistre le skill réellement chargé à chaque tour ; les corrections
enregistrent quand c'était le mauvais. Rien à construire pour savoir s'il y a un
problème : laisser gb travailler et lire `gk stats`. Corrections à zéro sur
plusieurs centaines de tours = la sélection marche = la phase 2 n'a pas lieu
d'exister.

### Ce qui survit dans tous les cas

Indépendant de la question de la recherche : la **mesure** (phase 1, déployée et
qui compte), les **faits sourcés** (phase 4, dont `RÈGLE ABSOLUE` datée et
attribuée est l'amorce), et le **rangement** — auditer les noms de skills écrits
à la main dans `DOMAINS`, signaler les orphelins et les skills trop gros.

### Le catalogue n'est ni complet ni tronqué : il est variable

Relevé dans `/opt/hermes/hermes_cli/prompt_size.py`, qui analyse le prompt
système et en livre donc la forme exacte :

```python
_SKILLS_BLOCK_RE = re.compile(r"<available_skills>.*?</available_skills>", re.DOTALL)
# A rendered skill entry is ``    - name: desc`` (or ``    - name``); category headers use two
# leading spaces, so the four-space + ``- `` prefix isolates skill lines.
# Posture-demoted categories render all visible skill names on one shared line.
```

Trois faits, et le troisième rouvre la phase 2 :

1. le catalogue existe, balisé `<available_skills>`, **groupé par catégories** ;
2. une entrée peut être rendue **sans description** (`    - name` seul) ;
3. une notion de **posture** *déclasse* des catégories entières : leurs skills
   sont alors réduits à une liste de noms sur une seule ligne. Le mot
   « visible » suggère en outre que certains peuvent ne pas apparaître du tout.

**Conséquence.** L'objection de GM — « nom + description suffisent » — est
juste, mais Hermes **ne garantit pas de fournir les deux**. Pour une catégorie
déclassée, gb choisit sur le nom seul : sans déclencheur, sans « ne pas charger
pour ». C'est le manque, et il est bien mieux ciblé que ce que la phase 2
prévoyait : il ne s'agit pas d'indexer 177 corps de skills, mais de **fournir la
description que le prompt système escamote**, pour les catégories qu'il
déclasse. Cela explique aussi pourquoi `skills_gate.py` a été écrit par-dessus
un catalogue qui liste déjà tout : il ne listait pas tout de la même façon.

**CORRIGÉ une heure plus tard, et la correction annule la conséquence
ci-dessus.** `/opt/hermes/agent/coding_context.py:410` :

```python
"""Skill categories the active posture demotes to names-only (empty outside ``focus``)."""
```

Le déclassement en noms-seuls n'existe **que dans la posture `focus`**, et la
liste est vide partout ailleurs. Hors de cette posture, chaque skill porte donc
bien sa description. Il n'y a pas de trou d'information à combler, et
l'objection de GM reprend toute sa force, avec une réserve de moins.

La méthode qui a produit l'erreur mérite d'être notée, parce qu'elle s'est
répétée trois fois dans la même journée : un mécanisme trouvé a été pris pour
l'explication, alors que la porteé réelle du mécanisme était écrite une ligne
plus loin. Avant de conclure d'un relevé, lire ce qui le borne.

Le constructeur est trouvé : `/opt/hermes/agent/prompt_builder.py:1370` et
`:1388`, deux chemins de rendu. `index_lines` à la ligne 1370 est ce que GM
appelle « l'étape d'index » du prompt système.

### Conclusion : la phase 2 devient petite, elle n'est pas supprimée

**Rectification d'une erreur de lecture, le même jour.** GM avait dit « notre
bibliothécaire a seulement besoin de nom + description ». C'était une consigne
de **simplification de l'index**, pas son abandon. La version précédente de
cette section concluait à la mort de la phase 2 : c'était une surinterprétation,
et elle changeait le but de GK sans que personne l'ait demandé.

Le relevé, lui, tient. `agent/prompt_builder.py` :

```python
if category in demoted:
    index_lines.append(f"  {category} [names only]: {', '.join(...)}")
    continue
...
index_lines.append(f"    - {name}: {desc}" if desc else f"    - {name}")
```

Le `config.yaml` de gb ne contient ni `posture` ni `focus`, donc `demoted` est
vide : **chaque skill est listé avec sa description**, et le prompt système
ordonne de charger au moindre doute (« you MUST load it », « err on the side of
loading »).

**Où le raisonnement avait cassé.** Il concluait qu'un index « ne ferait que
répéter ce qui est déjà là ». Faux : le catalogue **liste** les 177 skills, une
fois, dans un prompt stable ; GK **désigne** ceux qui concernent la demande du
moment. Lister et désigner sont deux actes différents — c'est la différence
entre un annuaire et un bibliothécaire. Et `skills_gate.py` est la **preuve
pour**, non contre : quelqu'un en production a écrit 350 lignes à la main pour
désigner quelques skills par-dessus un catalogue qui les liste tous. L'argument
avait été retourné dans le mauvais sens.

### Ce que la consigne « nom + description » supprime vraiment

Elle ne supprime pas l'index. Elle supprime tout l'édifice construit autour :

| Disparu | Pourquoi |
|---|---|
| Le transfert de ~3 Mo de corps | GK n'a plus besoin des corps. Le manifeste porte nom + description + empreinte, soit ~25 Ko |
| Le découpage par titre, le niveau 3, les pièges autoportants | Il n'y a plus de corps à découper |
| La colonne à poids négatif | Pas de section `Déclencheurs` à scinder |
| Le stemmer français et la question du corpus bilingue | 177 descriptions d'une phrase, pas 3,6 Mo de prose |
| FTS5, `content=''`, `contentless_delete`, la version de SQLite | 177 enregistrements courts tiennent en mémoire |
| Le corpus de banc tiré de `Historique des cas vécus` | Plus de moteur de recherche complexe à valider |
| L'indexation de `references/`, et sa réouverture éventuelle | Hors périmètre par construction |

Ce qui reste est un index de 177 lignes qu'on relit d'un coup d'œil. La phase 2
cesse d'être un chantier et devient une fonction.

### Le périmètre de l'index, arrêté : quatre champs

Décision de GM : ajouter les déclencheurs et les contre-indications à nom +
description. Ce sont exactement les deux apports que le tableau de la barre
réelle classait « vivants mais demandant le corps ».

| Champ | Source | Rôle |
|---|---|---|
| `name` | frontmatter | identité |
| `description` | frontmatter | déclencheur en une phrase |
| `declencheurs_positif` | `## Déclencheurs` (et variantes, `When to use`…) | quand charger, détaillé |
| `declencheurs_negatif` | la moitié « NE PAS », ou `## Ne pas charger si` | **à soustraire** |

**Le volume reste petit.** Le relevé sur 239 fichiers donnait 54 Ko de
`Déclencheurs` pour 33 sections, soit ~1,6 Ko chacune. Sur 177 skills, même si
tous en avaient une : moins de 300 Ko. On reste en mémoire, sans FTS5, sans
transfert de 3,6 Mo.

#### Le découpage négatif devient obligatoire

Une ligne réelle du relevé le montre :

> `Ne pas charger pour une commande client : voir prestashop-catalogue.`

Elle contient une négation **et** un pointeur vers un autre skill. Indexée comme
texte positif, elle fait remonter `prestashop-commande` sur « commande client »,
avec les bons mots au meilleur endroit possible. Tant qu'on n'indexait que la
description, l'inversion était théorique. En indexant `Déclencheurs`, **elle
devient certaine sur les cas les plus nets**. La colonne négative n'est donc
plus un raffinement : c'est ce qui empêche l'ajout de nuire.

#### Le plugin découpe, GK décide où

Le plugin ne doit pas savoir ce qu'est un déclencheur — l'étape de conception 5
pose qu'il ne comprend rien. Donc **GK lui envoie la liste des intitulés
voulus** (`Déclencheurs`, `When to use`, `Ne pas charger si`…), et le plugin
tranche le fichier sur ces chaînes et ne renvoie que ces tranches. Trois
bénéfices : GK ne voit jamais les corps, le transfert tombe à ~300 Ko, et
**ajouter une rubrique plus tard ne touchera plus au plugin** — ce sera une
ligne de configuration. C'est donc la dernière modification du plugin dont
l'index aura besoin (une recopie du pont et un redémarrage de gb).

#### Le poids reste un paramètre

Texte positif et négatif restés séparés, le score s'écrit
`match(demande, nom+description+déclencheurs) − λ·match(demande, négatif)`.
λ se règle sur la mesure. Avec 177 enregistrements en mémoire, le faire varier
est trivial, là où le même réglage sous BM25 demandait des poids de colonnes.

#### La réserve, à mesurer plutôt qu'à croire

Un `## Déclencheurs` est écrit pour un humain : il est long, et il cite
volontiers les domaines voisins pour s'en distinguer. Ces citations peuvent
faire matcher un skill sur les demandes de ses voisins. Ajouter `Déclencheurs`
**améliore donc le rappel et peut dégrader la précision**. Les champs restent
séparables pour comparer « description seule » et « description +
déclencheurs » sur les mêmes demandes, et trancher sur un chiffre.

Enfin, un skill sans `## Déclencheurs` retombe sur nom + description : l'index se
dégrade skill par skill, pas en tout ou rien. Et GK saura dire combien de skills
en portent une — un indicateur de qualité de la bibliothèque que personne ne
possède aujourd'hui.

### Ce que le relevé change quand même

**Le témoin de l'A/B est le catalogue complet.** L'étape 8 le décrivait comme
« GK contre rien » sur les domaines non couverts par le gate : il n'y a pas de
« rien ». GK doit améliorer une situation où gb voit déjà tout et reçoit déjà
l'ordre de charger. La barre est plus haute que ce que le document annonçait, et
il valait mieux le savoir avant de mesurer qu'après.

**L'hypothèse d'échec se précise.** Puisque l'information est complète et
l'ordre explicite, un mauvais choix de skill n'est pas un défaut
d'information : soit gb ne scanne pas, soit deux descriptions se confondent.
C'est exactement la distinction que la phase 1 mesure déjà —
`bad_recommendation` contre `brief_ignored`.

### Ce que GK garde

| | Pourquoi |
|---|---|
| **La désignation** (phases 2 et 3, allégées) | Le but de GK : désigner les skills pertinents au moment du tour, là où le catalogue se contente de les lister |
| **La mesure** (phase 1, déployée) | Seule chose qui dise si gb charge ce qu'il devrait, et laquelle des deux causes agit |
| **Les faits sourcés** (phase 4) | Indépendant de la recherche. `RÈGLE ABSOLUE` datée et attribuée en est l'amorce |
| **Le rangement** | Auditer les noms écrits à la main dans `DOMAINS`, signaler orphelins, doublons et skills trop gros |

### Le banc, mesuré : deux effets de signes opposés

Relevé du 2026-10-22, 259 cas tirés des phrases citées des déclencheurs, chacune
retirée de l'index de son propre skill avant d'être cherchée.

| Population | cas | rappel@1 | rappel@5 | MRR | MRR nom+description |
|---|---|---|---|---|---|
| **Métier** (`greenmusic`) | 175 | 0,509 | 0,720 | **0,592** | 0,375 |
| **Générique** | 84 | 0,310 | 0,512 | **0,391** | **0,455** |
| Informative | 233 | 0,459 | 0,661 | 0,541 | 0,419 |
| Peu informative | 26 | 0,308 | 0,577 | 0,397 | 0,243 |
| **Ensemble** | 259 | 0,444 | 0,653 | 0,527 | 0,401 |

**Le résultat central n'est pas le gain moyen de +0,126.** C'est que les
déclencheurs apportent **+0,217 de MRR au métier** et **en ôtent 0,064 au
générique**. Deux effets de signes opposés, qu'une moyenne faisait disparaître.

Le mécanisme se lit dans les échecs : `baoyu-comic` perd contre
`baoyu-infographic`, `claude-design` contre `gm-stripe-operations` et `linear`.
Les skills génériques se citent les uns les autres et nomment les mêmes outils —
c'est le risque de précision mesuré plus haut, concentré sur une seule
population.

**Ce que ça tranche.** L'objection de GM — « le bibliothécaire a seulement
besoin de nom + description » — est **fausse pour le métier** (0,375 contre
0,592) et **juste pour le générique** (0,455 contre 0,391). Elle avait raison
quelque part, et pas là où l'enjeu se trouve.

Et le gain ne vient pas du moteur, qui est une somme pondérée de quinze lignes
sans FTS5 ni BM25 : il vient des 296 phrases écrites à la main. **La valeur est
dans la bibliothèque ; l'outil la rend utilisable.** C'est ce qu'il faut dire aux
décideurs, et c'est vérifiable.

## Le seuil de confiance : le chiffre est bon, le corpus ne l'est pas

Relevé du 2026-10-23. Étape 4 de la liste de GM : savoir se taire.

| Corpus | sans seuil | seuil retenu | couverture (vérif.) | précision (vérif.) |
|---|---|---|---|---|
| Phrases citées, 259 cas | 0,448 | marge 0,25 **et** couverture 0,70 | 0,426 | 0,855 |
| Sessions synthétiques (suites), 44 cas | 0,682 | **couverture 0,60** | 0,864 | **0,895** |

Lu naïvement, l'objectif de GM est atteint : avec le contexte, GK parlerait sur
86 % des demandes en ayant raison 90 % du temps. **Quatre réserves, et la
dernière est rédhibitoire.**

**n = 44**, donc 22 cas en vérification dont 19 retenus. 17 bons sur 19 font
89,5 %, mais l'intervalle de confiance va d'environ 67 % à 98 %.

**C'est le corpus le plus favorable possible** : la seule condition `suite`,
alors que le taux de suite réel est de 0,29.

**Le score absolu a surajusté exactement comme prévu** : précision 1,0 sur
7 cas d'ajustement, puis **0,333 sur 3 cas de vérification**. C'est la
démonstration de pourquoi la moitié de vérification existe, et elle vaut d'être
gardée comme telle. La marge, elle, a perdu : son seuil retenu est `0,0`, donc
elle n'apporte rien et c'est la couverture seule qui porte tout.

**Et le seuil ne transfère pas.** La couverture mesure quelle part des mots de
la demande le gagnant porte. Une phrase citée fait 2 à 6 mots, donc en porter
60 % est facile. Une demande réelle a une médiane de 112 caractères, soit une
vingtaine de mots : aucun skill n'en portera 60 %. **Un seuil mesuré sur des
phrases courtes et appliqué à des demandes trois fois plus longues ne veut pas
dire la même chose.**

Cela se vérifie **sans aucune étiquette** : `gk seuil --reel` applique les
seuils aux demandes réelles et dit combien les franchiraient. Sans vérité
terrain il n'y a pas de précision à mesurer, mais la question posée est la
**transférabilité**, et elle n'en demande pas.

**Ce que ça implique pour la phase 3.** Le seuil ne peut pas être fixé sur le
corpus curé. Il demande une vérité terrain sur des demandes réelles — c'est-à-dire
soit des corrections de la phase 1 en nombre (6 à ce jour), soit un jugement
humain sur un échantillon. Tant que ni l'un ni l'autre n'existe, injecter un
brief revient à parier sur un seuil mesuré ailleurs.

## Le contexte de session : le meilleur résultat du projet

Relevé du 2026-10-23, sur 88 sessions synthétiques fabriquées depuis les phrases
citées — une citation sert de tour précédent, une autre de demande.

| Condition | cas | MRR sans | MRR avec | rappel@1 sans | rappel@1 avec |
|---|---|---|---|---|---|
| **Suite** (même skill) | 44 | 0,496 | **0,742** | 0,409 | **0,682** |
| **Bascule** (autre skill) | 44 | 0,496 | 0,467 | 0,409 | 0,386 |

**+0,246 quand la conversation reste sur le sujet, −0,029 quand elle change.**

Le seuil de rentabilité se calcule : 0,029 / (0,246 + 0,029) = **11 %**. Le
contexte est donc gagnant dès qu'un tour sur neuf reste sur le sujet. Il
faudrait que **89 % des tours changent de sujet** pour qu'il nuise, ce qui ne
décrit aucune conversation réelle. **Activé.**

C'est le meilleur résultat du projet, et de loin : le rappel en première
position passe de 0,41 à 0,68 sur les suites, là où quatre hypothèses de score
successives n'avaient rien déplacé de plus de quelques centièmes. Cela confirme
le découpage de GM : l'étape 3 était près de son plafond, et c'est l'étape 1 —
l'intention et le contexte — qui portait la marge.

**Comment il marche.** Les jetons des trois tours précédents, dans une fenêtre
de trente minutes, comptent 0,3 de ceux de la demande : ils orientent, ils ne
décident pas. Un test vérifie qu'une demande portant son propre domaine garde la
main. Les demandes précédentes sont lues des enregistrements déjà faits : rien
de nouveau n'est collecté pour cela.

**Deux réserves à garder.** Les sessions sont **fabriquées** : le chiffre indique
un sens, pas une amplitude. Et le solde réel dépend du taux de bascule, que
`gk demandes` estime désormais — une session qui ne charge qu'un seul skill
distinct est une conversation restée sur le sujet.

**Ce qui n'a pas été fait, et pourquoi.** Un bonus pour les skills **déjà
chargés** dans la session serait le signal de contexte le plus fort. Il n'est
pas mesurable : l'étiquette disponible sur les demandes réelles est « le skill
que gb a chargé », donc mesurer un bonus pour les skills chargés avec cette
étiquette serait circulaire. Il faudrait un jugement humain, ou une étiquette
indépendante.

## Le plafond est dans la bibliothèque, et il est mesuré

**Objectif fixé par GM le 2026-10-23 : le bon skill en première position 9 fois
sur 10.** La décomposition des échecs dit ce que cela demande.

| Cause de l'échec | Cas | Remède |
|---|---|---|
| `texte_absent` — le skill attendu ne marque **aucun point** | **48** | Rédaction |
| `texte_domine` — le gagnant porte tous ses jetons, et plus | **12** | Rédaction |
| `ordre` — les deux marquent, le classement se trompe | **29** | Score |

**67 % des échecs relèvent de la rédaction.** Donc en corrigeant *parfaitement*
les 29 échecs de classement, le rappel en première position passerait de 0,448 à
environ 0,56. **Les 9/10 ne sont pas atteignables par le moteur.** Ce n'est plus
une opinion, c'est une borne calculée.

Le travail utile n'est donc pas un réglage de coefficients, c'est une liste de
courses : `gk manques` rend, pour chaque skill, les demandes qu'il devrait
capter et **les mots qui manquent à son texte**.

### Quatrième hypothèse rejetée : la normalisation par la longueur

Elle **nuit** : 0,482 contre 0,531 de MRR. Et le détail corrige mon mécanisme à
moitié : elle **aide** le générique (0,392 → 0,422) et **abîme** le métier
(0,598 → 0,511). Le biais de longueur existait donc bien, mais le remède coûte
plus qu'il ne rapporte — et on ne peut pas l'appliquer à une seule population
sans rendre les scores incomparables. Laissée désactivée.

Quatre hypothèses de score testées, quatre rejetées : pondération par
population, retrait de la prose, normalisation par la longueur, et (plus tôt) la
déduplication des marqueurs de négation. Aucune n'a déplacé le résultat de plus
de quelques centièmes. C'est cohérent avec les 67 %.

### Des skills en double, trouvés par accident

`gk confusions` a rendu des paires d'un skill **avec lui-même**, proximité 1,0 :
`dogfood` et `codebase-inspection`. Les journaux de gb l'annonçaient déjà :
« 4 user-modified (kept): codebase-inspection, dogfood, powerpoint,
computer-use ». Hermes livre une version groupée, la version locale est gardée,
et **les deux coexistent dans l'arborescence**.

`prompt_builder` déduplique par nom, donc gb n'en voit qu'un. **GK indexe les
deux** : ils se concurrencent et se partagent l'IDF, ce qui abaisse le score des
deux. Les doublons ont désormais leur propre rapport, séparé des paires
simplement proches — qu'ils masquaient.

### Ce que la moitié négative devait faire, et ne fait pas encore

Remarque de GM : un skill est censé porter une description, une ligne de
déclenchement **et une ligne d'interdiction**. C'est juste, et c'est le
mécanisme de désambiguïsation prévu par la conception. Il est utilisé par **31
skills sur 186**. Deux skills que rien ne sépare sont donc deux skills dont l'un
n'a pas écrit son interdiction, et `gk confusions` dit pour chaque paire lequel
des deux en porte une.

## Le fossé de connaissance : GK n'avait jamais vu une demande réelle

**Constat de GM, le 2026-10-22, et il invalide la portée de tout ce qui précède.**

Tout ce qui a été mesuré ce jour-là — les 296 phrases citées, les descriptions,
les déclencheurs — vient de **textes écrits par les auteurs des skills**. Le
0,592 de MRR sur le métier répond donc à : « retrouve-t-on un skill à partir
d'une formulation que son auteur a imaginée ? » Il ne répond pas à : « à partir
de ce qu'un collègue tape réellement ? »

L'écart entre ces deux questions n'est pas mesuré, et il est probablement grand.
Les auteurs écrivent « contrôle des factures fournisseurs » ; un collègue tape
« pourquoi on a payé 40 balles de port chez Adagio ».

**Pourquoi le fossé existait.** Le pont était branché sur
`transform_llm_output`, qui porte la **réponse**, et sur `on_skill_lifecycle`,
qui porte le **skill chargé**. La demande de l'utilisateur ne passe que par
`pre_llm_call` — le hook écarté dès la phase 1, et qu'un test verrouillait comme
absent. GK était donc **structurellement aveugle aux demandes**. Ce n'était pas
un oubli : c'est la conséquence directe de la décision de ne rien toucher au
chemin d'entrée avant d'avoir une mesure.

### `pre_llm_call`, en observation seule

**Décision de GM.** Le pont transmet la demande, GK l'enregistre, et **rien
n'est injecté**. C'est le hook par lequel on injecterait un brief ; on n'en
utilise que la moitié, et cette moitié est verrouillée par des tests.

Trois propriétés, chacune tenue par un test plutôt que par la discipline :

* **les trois hooks rendent toujours `None`**, sur une entrée normale, sur une
  entrée absurde, et quand GK est injoignable ;
* `request` **ne rend aucun contenu** : la réponse ne porte que `v`, `ok`,
  `request_id` et `besoins`. La phase d'observation ne peut pas devenir une
  injection par accident ;
* `pre_llm_call` est **le seul hook sur le chemin critique** — il précède
  l'appel au modèle, donc tout ce qui attend là fait attendre l'utilisateur. Le
  pont envoie sur un fil détaché, ne lit jamais la réponse de GK, ne déclenche
  aucune ronde de catalogue, et rend `None` immédiatement.

**La donnée, et sa limite.** Aucun texte d'utilisateur n'était entré dans la
base jusqu'ici : c'est une ligne franchie consciemment. Le texte est **tronqué à
200 caractères** et **expire au bout de 90 jours**, comme les preuves de
l'étape 4 ; le compteur, sans texte, reste sans limite. `gk forget --demandes`
les efface, et `--before` comme `--conv` les couvrent.

**L'appariement est une corrélation, pas une vérité.** `gk demandes` rapproche
une demande du skill chargé dans les deux minutes qui suivent, par session —
faute de clef commune, `pre_llm_call` et `on_skill_lifecycle` n'en partageant
aucune. La commande le dit dans sa sortie, parce qu'un corpus étiqueté par
corrélation ne vaut pas un corpus étiqueté par construction.

**Ce que ça coûtera de savoir.** Le nom du champ portant la demande n'est pas
connu : le contrat du hook shell `skills_gate.py` annonce `user_message`, mais un
hook de plugin peut différer — la leçon de `response_text`, qu'aucune des sept
premières suppositions n'avait trouvé. Sept candidats sont essayés, et
`demande_introuvable` est journalisé si aucun ne marche.

### Trois hypothèses rejetées, et celle qui reste

| Bras | ensemble | métier | générique |
|---|---|---|---|
| Tous les champs | 0,527 | 0,592 | 0,391 |
| Nom + description | 0,401 | 0,375 | **0,455** |
| Déclencheurs métier seulement | 0,535 | 0,603 | 0,395 |
| Citations sans la prose | 0,494 | 0,544 | 0,388 |

**La prose apporte** : la retirer coûte 0,033 d'ensemble et 0,048 sur le métier.
Hypothèse rejetée. Et elle ne corrige pas le générique non plus : 0,388 contre
0,391, autant dire rien.

Mais cet échec élimine la dernière explication « par champ ». Ce n'est ni la
prose, ni les déclencheurs : pour les skills génériques, **nom + description
seuls battent toutes les combinaisons essayées**. Le facteur commun est donc
simplement **le fait d'ajouter du texte**.

Un seul mécanisme produit cela uniformément : un skill qui porte plus de jetons
distincts rencontre plus de demandes **par hasard**, même sur des mots peu
discriminants. Les skills génériques hérités de Claude sont longs et verbeux ;
les descriptions métier sont courtes et précises.

C'est la **normalisation par la longueur**, et elle avait été omise délibérément :
le module annonce « la présence, pas la fréquence », ce qui corrige la
répétition d'un mot *dans* un champ mais pas le nombre de mots différents que ce
champ contient. C'est le biais que BM25 corrige par sa longueur moyenne de
document — précisément la partie de BM25 jugée inutile ici.

Le facteur est rapporté à la moyenne du champ, pour que les poids gardent leur
sens : il vaut 1 à la longueur moyenne, plus pour un champ court, moins pour un
champ long. Il s'applique à **tous** les skills, donc il ne touche pas à la
comparabilité des scores — contrairement à la pondération par population.

Si ce bras résorbe l'inversion du générique, le défaut était la **longueur** et
non la catégorie, et aucune règle par population n'est nécessaire.

### Pondérer par population : mesuré, puis retiré

La règle qui semblait découler du relevé — indexer les déclencheurs du métier
seulement — a été mesurée avant d'être adoptée. Elle est **mal fondée**, et la
raison vaut d'être gardée.

| Bras | ensemble | métier | générique |
|---|---|---|---|
| Tous les champs | 0,527 | 0,592 | 0,391 |
| Nom + description | 0,401 | 0,375 | **0,455** |
| Déclencheurs métier seulement | 0,535 | 0,603 | 0,395 |

Le bras mixte devait donner au générique le traitement qui lui réussit
(nom + description, 0,455). Il rend 0,395. **Parce qu'il ne retire les
déclencheurs qu'aux génériques, qui concourent alors avec un champ en moins
contre des adversaires qui gardent tout.** Les échelles de score ne sont plus
comparables : un skill noté sur quatre champs bat mécaniquement un skill noté
sur deux. Dans le bras nom+description, personne n'a de déclencheurs, donc la
compétition est uniforme.

Et le +0,011 gagné sur le métier n'est pas une amélioration de la recherche :
c'est l'affaiblissement de ses concurrents. Un artefact.

**Règle qui en découle, et qui est désormais tenue par un test** : tous les
skills sont notés sur les mêmes champs avec les mêmes poids. Un texte identique
doit donner un score identique, quelle que soit la catégorie. La catégorie sert
à découper les résultats du banc, jamais à pondérer.

Le problème du générique n'est donc pas que ses déclencheurs soient indexés,
c'est qu'ils se citent les uns les autres. Le remède est dans le texte, pas dans
les coefficients. **Le bras mixte est remplacé par un bras « citations sans la
prose »** : les citations sont des phrases d'utilisateur, propres, tandis que la
prose est verbeuse et c'est là que vivent les renvois vers les voisins. Celui-là
ne touche pas à la comparabilité des scores, puisqu'il s'applique à tous les
skills.

**Une affirmation corrigée.** La première lecture du banc disait qu'aucune
recherche par les mots ne pouvait servir les demandes sans vocabulaire de
domaine. Les données la contredisent : ces 26 demandes obtiennent 0,577 de
rappel@5, et les déclencheurs les aident de +0,154. L'affirmation valait pour
« étape 1 validée, go » prise isolément, pas pour le décile entier. Elle était
dans le texte que la commande imprime, donc elle aurait voyagé.

**Ce que le chiffre ne permet pas encore.** Un rappel@5 de 0,720 sur le métier,
avec un corpus curé donc optimiste, ne suffit pas à fonder un brief injecté. La
phase 3 ne se décide pas sur ce seul chiffre, et le remède aux 88 échecs de
classement est à chercher d'abord dans les descriptions — un travail de
rédaction — avant de régler des coefficients.

### Les déclencheurs sont des citations de l'utilisateur

Relevé le 2026-10-22 en lisant un skill réel, `gm-supplier-invoice-control` :

```
## Quand charger ce skill
- « contrôle des factures fournisseurs », « vérifier si les fournisseurs nous
  facturent bien le PA / le port »
- « point dettes », « point compta », « où en sont les factures fournisseurs
  en attente »
- Audit d'écart entre ce qu'on croit payer (Presta) et ce qui est facturé
```

Ce ne sont pas des descriptions de déclencheurs : ce sont **les phrases de
l'utilisateur, entre guillemets**. Le texte indexé n'est donc pas « du texte en
plus », c'est du texte **de la même forme que les demandes**.

**Conséquence pour le score** : les lignes entre guillemets méritent le poids le
plus élevé de tous, au-dessus de la prose qui les entoure. Et c'est un signal
**structurel** — la présence de guillemets — non une devinette. `gk declencheurs`
les compte désormais (`skills_avec_exemples_cites`,
`lignes_entre_guillemets`).

La `description` va dans le même sens : « PA », « frais de port », « dettes »,
« Qonto », « Pennylane », « proforma (PRE) », « facture finale (IV) ». Du
vocabulaire réel, pas un résumé abstrait. **Le pari « sans embeddings » de
l'étape de conception 6 est désormais appuyé par des données**, et non plus par
la seule lecture du squelette.

### Le risque de précision était surestimé

La première mesure annonçait « 16 skills sur 65 (25 %) citent un autre skill »,
présenté comme le risque de précision de l'ajout des déclencheurs. Le chiffre
confondait deux choses :

* **mentionner un outil** — `pennylane`, `prestashop`, `linear`, `notion`,
  `himalaya` sont des outils qui ont un skill portant leur nom. Les nommer,
  c'est nommer son domaine, et c'est exactement le vocabulaire qu'on veut
  indexer ;
* **renvoyer vers un voisin** — « sinon voir gm-presta-catalogue ». Là oui, la
  ligne fait remonter le skill sur les demandes de son voisin.

Les deux sont maintenant comptés séparément, le renvoi étant repéré par un
marqueur (`voir`, `vs`, `plutôt`, `instead`…) ou par son appartenance à la
moitié négative. Le risque réel est donc plus petit que ce que le document
annonçait, et la consigne de rédaction reste la même : un renvoi appartient à la
moitié négative, où il sert l'humain sans polluer l'index.

### Le tri des déclencheurs, mesuré : la colonne négative est gardée

Relevé du 2026-10-22 sur les 65 sections reçues, après correction du comptage.

| Mesure | Valeur |
|---|---|
| Skills avec des déclencheurs | 65 sur 182 (36 %) |
| Contre-indication **structurelle** (titre dédié) | **3** |
| Contre-indication **en prose** (strict) | **18** |
| En prose, comptage large | 21 |
| Sans contre-indication | 41 |
| Poids positif / négatif | 48 Ko / 20 Ko |
| **Skills citant un autre skill** | **16 sur 65, soit 25 %** |

**Décision : on garde la colonne négative.** 21 skills en strict, soit 12 % du
catalogue, ce qui la place dans la bande « marginale, à garder seulement si elle
est gratuite ». Et elle l'est : les tranches brutes sont déjà en base, le
découpage se calcule à l'indexation, la soustraction est un coefficient dans la
requête. Aucune table, aucun transfert, aucun mécanisme séparé. Elle devient de
plus **nécessaire** dès que les déclencheurs sont indexés, sans quoi une ligne
« ne pas charger pour X » fait remonter le skill sur X.

**Un pronostic faux, à noter.** J'annonçais que la correction du chevauchement
des marqueurs ferait tomber le chiffre sous 10 % et conduirait à l'abandon. Elle
ne l'a fait passer que de 13 à 12 % : `jamais` n'était le marqueur **unique** que
pour 3 skills, les autres portant aussi un `ne pas` ou un `ne jamais`. La
correction était juste, le pronostic ne l'était pas.

**Les 25 % de citations croisées sont le vrai sujet.** `gm-amazon-seller-central`
cite `prestashop` dans ses déclencheurs, `gm-supplier-invoice-control` cite
`pennylane`. Indexées comme texte positif, ces lignes font remonter le mauvais
skill. Deux conséquences : c'est à surveiller au banc, et c'est une consigne de
rédaction — un renvoi vers un voisin appartient à la moitié négative
(« ne pas charger pour les fiches produits : voir gm-presta-catalogue »), où il
reste utile à l'humain et cesse de polluer l'index.

**Et trois contre-indications structurelles sur 182 skills** : tout repose
aujourd'hui sur le reniflage de marqueurs. C'est l'argument chiffré de la
demande faite à `gm-skill-template` — un titre dédié transforme une devinette
en lecture, et il n'y a que trois skills à ne pas rattraper.

### Le catalogue, mesuré en production le 2026-10-22

Premier relevé réel après la mise en service du facteur. Il remplace les
estimations de la journée.

| Mesure | Valeur |
|---|---|
| Skills au catalogue | **182** (disque 185, dont 3 que Hermes considère inactifs) |
| Indexes | 182, `a_indexer` à 0 |
| **Skills avec une section de déclencheurs** | **65, soit 36 %** |
| Poids des sections | **66 Ko**, ~1 Ko par skill |
| Durée d'une ronde complète | **0,19 s**, 182 tranches en 5 tours |
| `sans_description` | 0 — `name` et `description` sont bien partout |

Répartition : `greenmusic` 86, `devops` 24, `productivity` 20,
`software-development` 18, puis une longue traîne de categories à 1-6 skills.
Le métier GM est donc 47 % de la bibliothèque, et la moitié restante est de
l'outillage générique — ce qui confirme que le préfixe `gm-` sépare deux
populations et que le banc doit rendre deux chiffres.

**Les 66 Ko invalident deux inquiétudes de la journée** : le transfert de corps
par MCP (il n'y en a pas, et les sections tiennent dans un appel) et la version
de SQLite (aucun FTS5 nécessaire). Ce qui restait des « trous de conception »
de la phase 2 n'existe plus.

**Et les 36 % sont le chiffre qui compte.** Pour **117 skills sur 182**, l'index
ne portera que `name` et `description` : il n'y a pas de déclencheurs rédigés à
indexer. Cela valide la consigne de GM — un index qui dépendrait des
déclencheurs serait bancal sur les deux tiers de la bibliothèque — et cela
donne au gabarit `gm-skill-template` une cible mesurable : faire monter ces
36 %.

**La question du verrou, tranchée.** Après le `chown`, `host-gateway.json` est
réécrit en `1001 1001` : gb réécrit le contenu sans recréer le fichier, donc le
propriétaire est préservé et la réparation tient. Mais les verrous de jetons
Discord et Telegram sont **recréés en `root:root` à chaque redémarrage** : un
composant de gb qui gère les jetons de plateforme tourne donc en root. Cela ne
casse rien aujourd'hui, le même composant les relisant ; c'est une fragilité de
Hermes à signaler, qui redonnerait la panne du matin si une version faisait
passer cette lecture en 1001.

### L'incident du 2026-10-22 : gb a cessé de répondre

À consigner parce que c'est la panne la plus coûteuse du projet, qu'elle vient
d'un défaut de procédure et non de conception, et qu'elle a une partie non
expliquée qui peut recommencer.

**La chaîne.** `docker compose exec` entre dans le conteneur **en root**
(`uid=0(root)`, relevé), pas en 1001 comme le processus de gb. La copie de
`catalogue.py` a donc créé un fichier `root:root` en mode 666 dans `/opt/data`.
Au redémarrage, gb a refusé son propre fichier de verrou de passerelle —
`ignoring host record .../host-gateway.json: owned by uid 0 (expected 1001)`,
dernière ligne du démarrage — n'a pas fini de démarrer, l'API n'a pas écouté
le port du healthcheck, et **gb a cessé de répondre**. Un
`chown -R 1001:1001` sur les verrous et un redémarrage l'ont remis.

**Pourquoi maintenant et pas avant.** Les étapes précédentes écrasaient des
fichiers existants, et `cat >` préserve le propriétaire d'un fichier qui existe.
La phase 2b est la première à **créer** un fichier neuf dans `/opt/data`. Le
défaut était là depuis le début et attendait son occasion.

**Ce qui reste inexpliqué, et qui peut recommencer.** `host-gateway.json` n'a
jamais été touché par nous, et les verrous de jetons Discord et Telegram en
`root:root` datent du redémarrage **précédent**. Quelque chose dans ce conteneur
crée des fichiers root dans `/opt/data` de lui-même — vraisemblablement un
composant de gb qui tourne en root là où un autre attend 1001. Si c'est le cas,
le problème reviendra à chaque redémarrage, indépendamment de GK, et il faudra
soit un `chown` systématique soit une correction côté Hermes. À vérifier en
relevant le propriétaire de `host-gateway.json` après un redémarrage où GK n'a
rien copié.

**Trois corrections de procédure.** Tous les `exec` du runbook passent par
`-u 1001`, la seule exception étant un `chown` de réparation ; une vérification
de propriétaire suit toute copie dans `/opt/data` et précède tout redémarrage ;
et le runbook porte désormais la neutralisation d'urgence du pont (un
`register()` qui ne fait rien), qui rend gb identique à ce qu'il était avant GK
sans toucher à `config.yaml`.

**Et une leçon sur la fréquence des redémarrages.** Un redémarrage de gb coûte
deux à trois minutes pendant lesquelles l'équipe ne peut pas travailler. Cela
n'avait jamais été dit dans la conception, et ce n'est pas un détail
d'exploitation : c'est la raison pour laquelle `catalogue.py` est désormais
**relu à chaud** quand son horodatage change, et pourquoi toute la logique
susceptible de bouger y a été descendue. Le coût d'un changement fait partie de
la conception.

### Deux relevés de la mise en service du facteur

**L'enveloppe de `call_mcp`.** `hermes_cli/plugins.py`
(`PluginContext._mcp_envelope`) normalise tout résultat MCP en une enveloppe :

```python
{"ok": True,  "result": <charge utile>}          # cas normal
{"ok": True,  "result": ..., "structuredContent": ...}
{"ok": False, "error": ...}                      # erreur rapportée
{..., "truncated": True}                         # au-delà de 64 Ko
```

Conséquence à écrire plutôt qu'à subir : **une charge utile qui porterait
`error` ou `result` au premier niveau serait mangée par l'enveloppe.** Les
outils de GK nomment leurs champs en français (`erreur`, `ok`), ce qui les
protège par accident. C'est désormais une règle : aucun outil de GK ne rend un
champ nommé `error` ni `result` au premier niveau.

Le premier jet du pont sondait `("wanted", "besoins", "ok", "v")` et rendait le
dictionnaire dès qu'une clef y figurait. `ok` appartient à l'enveloppe : il
rendait donc l'enveloppe en croyant rendre la charge, `wanted` valait `None`, et
la première ronde a envoyé 182 skills, reçu la réponse, n'en a rien compris et
journalisé `restants: 0` — ce qui se lit comme un succès. Le test de bout en
bout passait parce qu'il rendait la charge **nue** : il ne reproduisait pas la
production, donc il ne pouvait pas voir le défaut.

**`on_skill_lifecycle` ne porte pas de `turn_id`.** Relevé par le journal du
pont, ses kwargs réels :

```
action, provenance, reuse_after_patch, reused, session_id, skill_name,
task_id, telemetry_schema_version, use_count
```

Or `transform_llm_output` porte `{model, platform, response_text, session_id,
telemetry_schema_version, turn_id}`. **Les deux événements qu'il faut rapprocher
n'ont aucune clef commune hors `session_id`** : `turn_id` n'existe que d'un
côté, `task_id` que de l'autre.

Donc l'attribution exacte par `turn_id` — conçue, écrite, migrée (migration 2)
et testée — **ne peut structurellement pas fonctionner**. `skill_uses.turn_id`
restera vide et l'attribution retombe toujours sur « la correction en attente
la plus récente de la session », c'est-à-dire exactement ce qu'elle devait
corriger. La colonne garde son sens du côté `corrections`, où elle identifie le
tour d'où vient l'aveu.

Ce que vaut le repli : `on_skill_lifecycle` précède la réponse, donc le dernier
skill chargé avant une correction est vraisemblablement le bon. Il devient faux
si gb traite deux tours en parallèle dans une même session — ce qu'on ne sait
pas encore.

### La barre réelle, et ce que GK peut encore ajouter

GK doit améliorer une situation où gb voit déjà tout et reçoit déjà l'ordre de
charger. La barre tient en trois éléments : le **catalogue complet**
(`nom : description`, groupé par catégories décrites), une **instruction
maximaliste** (scanner avant de répondre, charger si même partiellement
pertinent, « err on the side of loading »), et le fait que ce bloc vit dans le
**« stable tier »** du prompt (`context_breakdown.py:49`) — donc identique à
chaque tour, dans le préfixe mis en cache.

C'est ce troisième élément qui ouvre la brèche.

| Apport possible | Déjà dans le prompt ? | Verdict |
|---|---|---|
| Savoir quels skills existent | **Oui, exhaustif** | Mort |
| Le déclencheur en une phrase | **Oui**, c'est la `description` | Mort |
| Le déclencheur détaillé (`## Déclencheurs`) | Non, il est dans le corps | Vivant, mais demande d'indexer le corps |
| Les contre-indications | Non, et l'instruction pousse à charger | Vivant, même remarque |
| **Proximité** : 3 lignes contre la question au lieu de la ligne 94 d'une liste | **Non** | Vivant, et le plus solide |
| **Désambiguïsation** entre deux voisins qui matchent tous les deux | Non : le catalogue les liste à égalité | Vivant |
| Quel fichier de `references/` lire | Non | Vivant, petit |
| Les faits sourcés | Non | Vivant, indépendant |

**Le levier principal n'est pas informationnel, c'est l'attention.** Scanner 177
lignes avant chaque réponse est un coût, et un bloc rigoureusement identique à
chaque tour s'apprend à survoler. Ce n'est pas une spéculation : `skills_gate.py`
accole son contexte **au message utilisateur** et non au prompt système. Son
auteur le justifie par le cache de prompt, mais l'effet est aussi de le placer
là où il sera lu. Quelqu'un en production a jugé le catalogue stable
insuffisant, et a atteint la proximité comme remède.

Noter la fragilité de cette position : l'attention est l'hypothèse qui reste
debout après élimination des autres. Elle doit être mesurée avant d'être
défendue.

#### Le risque jamais nommé : GK peut nuire

L'instruction dit « charge au moindre doute ». Un brief qui désigne trois skills
peut agir comme un **plafond** : gb charge les trois nommés et ne cherche pas le
quatrième, qui était le bon. GK transformerait une consigne large en sélection
étroite.

Deux conséquences de conception. La formulation du brief est **explicitement
additive** — jamais « ces skills », toujours « ces skills semblent pertinents,
en plus de ce que tu juges utile ». Et **l'A/B doit pouvoir détecter une
dégradation**, pas seulement un gain : l'étape 8 ne l'avait conçu que pour
mesurer une amélioration, ce qui est un biais de conception.

#### Le second mode de panne, que personne ne mesure

« Err on the side of loading » a un coût que rien ne compte. Médiane 13-20 Ko
par skill, maxima à 46 : trois gros skills chargés font plus de 30 000 jetons.
Si gb surcharge, personne ne le sait — et GK est le seul à pouvoir le savoir,
puisque `on_skill_lifecycle` enregistre déjà chaque chargement.

GK a donc peut-être **deux métiers opposés**, et le second est plus facile à
gagner : non pas faire charger davantage, mais chiffrer le coût d'une
instruction délibérément biaisée vers le chargement, et le réduire.

#### L'expérience tourne déjà

`skills_gate.py` injecte un contexte à côté du message utilisateur pour **12
domaines sur ~40**. C'est structurellement le **bras de traitement** de l'A/B
qu'on voulait monter, et il tourne en production depuis des mois. La phase 1
enregistre désormais les chargements de skills par tour.

**On peut donc comparer les taux de chargement entre tours couverts et tours non
couverts sans écrire une ligne de GK.** Si la proximité agit, cela se verra là.
Si elle n'agit pas, l'hypothèse centrale de GK tombe avant qu'on ait construit
l'index. Réserve à dire : les 12 domaines du gate ne sont pas tirés au sort, ce
sont les plus chauds, donc la comparaison est confondue. Elle est informative,
gratuite, et arrive des mois avant un vrai A/B.

#### Trois chiffres, dans cet ordre, avant d'écrire le brief

1. **Combien de tours sans aucun skill chargé.** Objectif, indépendant de tout
   aveu de gb, calculable depuis les données déjà collectées. Dit s'il existe un
   problème de chargement.
2. **Combien de skills chargés par tour, et leur poids en jetons.** Dit si le
   problème est l'inverse : la surcharge.
3. **Le taux de chargement, domaines couverts par le gate contre non couverts.**
   Teste l'hypothèse de proximité avec un traitement déjà en production.

Aucun ne demande de nouvelle collecte. Les trois ensemble décident si le brief a
un métier, et lequel.

### La dernière inconnue, fermée

`prompt_builder.py:1259` :

```
``compact_categories`` (coding posture) demotes categories to a names-only line
— nothing is ever hidden.
```

**Rien n'est jamais caché.** Le catalogue est exhaustif. `hidden_note` concerne
les skills enregistrés par des plugins (`plugins.py:1002` : « Not in
`~/.hermes/skills/` nor `<available_skills>` — explicit loads only »), qui ne
sont pas des fichiers de la bibliothèque. Aucun trou, donc : l'abandon de la
phase 2 est sans réserve.

### Le chiffre à produire ensuite

gb dispose de toute l'information et d'un ordre explicite de charger. Si
`skills_gate.py` a dû être écrit par-dessus, c'est que l'ordre n'est pas suivi —
et personne n'a jamais mesuré à quelle fréquence.

La phase 1 collecte déjà les deux moitiés du chiffre sans le calculer :
`observe` compte les tours, `skill_lifecycle` enregistre les skills chargés. La
différence donne **le nombre de tours où gb a répondu sans charger aucun
skill**, malgré le « you MUST load it » du prompt système. Aucune nouvelle
collecte n'est nécessaire, seulement une requête de plus dans `gk stats`. C'est
probablement la mesure la plus parlante que GK puisse rendre, et elle remplace
tout le banc d'essai de recherche abandonné.

Note aussi : la branche `is_single_query_session()` rend un prompt différent et
plus court (`ONESHOT_SKILLS_LOAD_GUIDANCE`), sans le bloc d'instructions. Les
sessions à requête unique ne reçoivent donc pas la même consigne de chargement.

Et cela renchérit sur la décision « retirer l'index du prompt système » :
retirer ce bloc ne retirerait pas seulement un catalogue mis en cache, mais
aussi l'instruction « you MUST load it » qui l'accompagne.

**Deux inconnues fermées au passage.** `plugins.py:940` définit
`register_system_prompt_section` : un plugin **peut** ajouter une section au
prompt système, ce qui restait ouvert à l'étape de conception 5. Et le code des
skills vit dans un paquet `agent` (`from agent.skill_utils import ...`), non
dans `hermes_cli` : c'est pourquoi le constructeur du catalogue est resté
introuvable à trois reprises.

---

## Le premier jugement humain, et le defaut de l'instrument

Releve du 2026-10-23, sur 50 demandes reelles annotees a la main. **C'est le
premier chiffre du projet qui ne vienne pas d'un texte ecrit par les auteurs
des skills.**

| | |
|---|---|
| Cas lus / annotes / sans reponse | 50 / 47 / 3 |
| Reponses « aucun ne convient » | **17, soit 36 %** |
| Le bon skill designe au rang 1 | **12** |
| Le bon skill designe dans les cinq | 30 |

### Ce que la commande a annonce, et pourquoi c'etait faux

La sortie disait `rappel_1_reel: 0.255` et `rappel_5_reel: 0.638`. Le second
chiffre **ne mesurait rien**, et l'arithmetique le montre : 0,638 + 0,362 =
1,000, exactement. Ce n'est pas une coincidence.

Le format v1 n'offrait que deux reponses : un rang parmi les cinq, ou `0`. Donc
**toute reponse non nulle designe un des cinq par construction**, et
`rappel_5 = 1 - part_sans_reponse` est une identite, pas une mesure. J'ai
ecrit un instrument qui ne pouvait pas repondre a la question pour laquelle il
etait fait.

Deuxieme consequence, du meme defaut : `0` portait **deux sens incompatibles**
-- « aucun skill de la bibliotheque ne sert cette demande » et « aucun de ces
cinq-la ». La lecture imprimee tranchait pour le premier (« c'est un trou de la
bibliotheque »), sans que rien dans les donnees l'etablisse. Les 36 % ne sont
donc **pas** un taux de trous de la bibliotheque : c'est un taux de `0`, et sa
composition est inconnue.

### Ce qui tient quand meme

`rappel_1` reste valide -- l'annotateur a bien choisi lequel des cinq etait le
bon -- mais son **denominateur** devient une question ouverte. Soit `x` le
nombre des 17 `0` dont le bon skill existait hors des cinq :

| | Borne basse (`x` = 17) | Borne haute (`x` = 0) |
|---|---|---|
| Rappel@1 sur les demandes servables | 12/47 = **0,255** | 12/30 = **0,400** |
| Rappel@5 sur les demandes servables | 30/47 = 0,638 | 30/30 = 1,000 |

**Le bon skill arrive premier entre 25,5 % et 40,0 % du temps sur de vraies
demandes.** Le banc, sur les phrases citees, donnait 0,448.

C'est le resultat important, et il est **meilleur que craint**. Le fossé de
connaissance ouvert le 2026-10-22 -- « tout est mesure sur des textes ecrits par
les auteurs » -- coute **au mieux 0,048** de rappel@1, et au pire 0,193. Le banc
mesurait donc quelque chose de reel, pas un artefact. C'est ce qui rend les
relevés precedents reutilisables, le contexte de session en tete.

Et une borne qui ne depend pas de `x` : avec 12 bons au rang 1 sur 47 demandes
annotees, **l'objectif de 9 sur 10 est hors de portee de deux ordres de
grandeur differents** -- le plafond de redaction calcule a 0,56, et le taux de
demandes que la bibliotheque ne sert pas, quel qu'il soit.

### Le taux de citation, lui, ne depend d'aucune hypothese

Question de GM : « il arrive en top 1 entre 26 % et 40 %, mais il est cite a
hauteur de combien ? » La reponse se compte au lieu de s'inferer, donc elle est
ferme.

| | Valeur | Statut |
|---|---|---|
| Le bon skill **cite dans les cinq** | 30 / 47 = **0,638** | **certain**, c'est un comptage |
| Et **premier** quand il est cite | 12 / 30 = **0,400** | **certain** |
| Produit des deux | 0,255 | = la borne basse du rappel@1 |

Deux lectures, et il faut les separer. **Comme mesure de recherche**, 0,638 est
une borne basse : son denominateur inclut les 17 demandes sans reponse connue,
donc le vrai rappel@5 est entre 0,638 et 1,000. **Comme taux de citation**,
0,638 est exact et c'est le chiffre qui compte pour un brief -- **un brief ne
sait pas si une demande a une reponse**, il affronte les 47.

Et le second chiffre est le plus parlant du releve : **quand le bon skill est
cite, il n'est premier que deux fois sur cinq.** Les trois autres fois il est
enterre aux rangs 2 a 5. C'est coherent avec les 67 % d'echecs de redaction : le
texte suffit souvent a faire entrer un skill dans la liste, rarement a le faire
gagner.

`juger` rend desormais `distribution_des_rangs`, parce qu'un bon skill au rang 2
et un au rang 5 ne se corrigent pas pareil -- le premier par des coefficients,
le second a peine.

### Correction : le brief a cinq entrees ne depend pas de `x` non plus

L'affirmation precedente -- « seul un brief a cinq entrees depend de `x` » --
etait trop genereuse. Le risque d'un tel brief est son taux de **mauvaise
direction** : proposer cinq skills dont aucun ne convient. C'est exactement les
17 cas, soit **36 % des demandes reelles, et ce chiffre est certain**. `x`
n'explique que la *cause* (trou de la bibliotheque ou echec de rappel), donc il
oriente le remede, pas la decision d'y aller.

**Donc aucune des deux formes de brief ne demande la reprise**, et un risque
supplementaire apparait a le dire ainsi : une partie des 17 sont des demandes
qui n'appellent legitimement aucun skill. Un brief qui en propose cinq y est
**pire que le silence** -- il invite gb a charger la ou il a raison de
s'abstenir. Or gb s'abstient dans 21 sessions sur 63. **L'effet principal d'un
brief pourrait etre de detruire des abstentions correctes**, ce qu'aucune mesure
du projet ne surveille aujourd'hui.

### La correction, et pourquoi elle ne refait pas l'heure

Le format v2 ajoute une troisieme reponse : **le nom du bon skill**, quand il
existe hors de la liste montree. Elle ne coute rien a l'annotateur -- un nom au
lieu d'un chiffre -- et elle resout `x`, les 36 %, et le rappel@5 d'un coup.
`gk catalogue --noms` rend la liste pour la retrouver.

Et `gk reprise` ne reprend **que les cas `0`** : pour chacun, il montre la
**suite du meme classement** (rangs 6 a 15, contexte de session reproduit), ce
que la v1 ne pouvait pas exprimer. Dix-sept cas au lieu de cinquante, et les
deux fichiers se relisent concatenes -- `cat echantillon.txt reprise.txt | gk
juger` -- le second corrigeant le premier.

### Les quatre regles qui en sortent, chacune tenue par un test

* **Un chiffre faux coute plus cher qu'un chiffre absent.** Sur un echantillon
  v1, `juger` ne rend plus de point : il rend un intervalle, et il refuse
  d'emettre `rappel_5_reel`. La lecture met l'ambiguite **avant** les chiffres.
* **Le denominateur est le nombre de demandes qu'un skill peut servir**, jamais
  le nombre de demandes annotees. Une demande que rien ne sert n'est pas un
  echec de recherche.
* **Un indicateur dont la valeur est une identite arithmetique doit etre
  detectable comme tel.** Le test de non-regression fait varier le rappel@5 **a
  nombre de `0` constant** : si les deux bougeaient ensemble, l'identite serait
  de retour.
* **Une demande multiligne ne doit pas casser le format.** Le titre `## N. ...`
  porte la demande, et un message d'utilisateur contient des retours a la ligne
  -- dont les suivantes pouvaient etre lues comme des candidats. La demande est
  desormais repliee sur une ligne. C'est peut-etre l'origine des 3 cas non
  annotes.

**Ce qui reste a faire, et c'est quinze minutes** : annoter les 17 cas de la
reprise. Apres quoi le rappel@1 et @5 reels sont des points, le seuil se refixe
sur de vraies demandes, et la phase 3 se decide sur des chiffres et non sur une
borne.

## La phase 3 : la decision, et la mesure qui la precede

Releve du 2026-10-23, apres le premier jugement humain. **Question posee par GM :
prend-on le risque de brancher ?**

### Les chiffres consolides

| Mesure | Valeur | Corpus |
|---|---|---|
| Rappel@1 **reel** | **0,255 a 0,400** | 47 demandes reelles, jugement humain |
| Rappel@1 au banc | 0,448 | 259 phrases citees par les auteurs |
| Plafond du moteur | 0,56 | calcule : 67 % des echecs sont de redaction |
| Demandes sans reponse dans la bibliotheque | 36 % | jugement humain |
| Rappel@1 avec contexte, sur une suite | 0,682 | 44 sessions **fabriquees** |
| Part des demandes franchissant le seuil | 7,7 % | demandes reelles |
| Sessions sans aucun chargement de skill | 21 / 63 | production |

### La decision sur le brief qui nomme un skill : non

Le bon skill arrive premier entre 26 % et 40 % du temps sur de vraies demandes.
gb voit deja ses 186 descriptions, recoit l'ordre de charger, et **deux autres
producteurs ecrivent deja dans `pre_llm_call`** (`gm-routing` et le hook shell
`skills_gate.py`). Injecter un troisieme bloc qui designe le mauvais skill six a
sept fois sur dix, avant que gb lise la demande, c'est ajouter du bruit a un
filet qui fonctionne.

**La conclusion tient aux deux bouts de l'intervalle**, donc elle ne demande
aucune mesure supplementaire. C'est la premiere decision du projet qui se prenne
contre la conception initiale, et elle se prend sur des chiffres.

### Ce que la reprise aurait achete, et pourquoi elle est abandonnee

Verification faite decision par decision : **une seule** depend de `x`, le
nombre des 17 `0` qui cachent un skill existant hors des cinq.

| Decision | Depend de `x` ? |
|---|---|
| Brief qui nomme **un** skill | non : 0,26 comme 0,40 donnent un refus |
| Retirer le catalogue du prompt systeme | non, et c'est pire qu'avant |
| Refixer le seuil | non : mort sans annotation (7,7 %) |
| Brief qui propose **cinq** skills | **oui**, et c'est la seule |

Donc « quinze minutes debloquent tout » etait faux : seul un brief a cinq
entrees en depend, parce que son risque est le rappel@5 -- a 1,000 il contient
toujours le bon skill, a 0,638 il pointe ailleurs dans 36 % des cas ou il
existe.

Et la reprise sur un echantillon **v1** est a moitie fiable : elle retrouve la
session en comparant le texte de la demande, or un fichier produit avant le
repli des demandes multilignes ne porte que la premiere ligne dans son titre.
Le classement montre ne serait alors pas la suite de celui qui a ete annote.
L'approximation est desormais comptee (`apparies_par_prefixe`) plutot que
cachee, mais elle reste une approximation.

**Le moyen court retenu** : lire les 17 demandes. Celui qui connait la
bibliotheque sait d'un coup d'oeil si un skill devrait exister, et
« ce skill manque » est une information que le rang ne porte pas.

```bash
awk '/^## /{t=$0} /^REPONSE:/{if ($2=="0") print t}' echantillon.txt
```

### La mesure qui n'avait jamais ete faite : GK contre gb

**Le defaut de tout ce qui precede.** Chaque chiffre du projet compare GK a une
**etiquette**. Aucun ne compare GK a **gb**. Or la phase 3 remplace un
jugement de gb par un jugement de GK : la seule question qui decide est
« lequel des deux se trompe le moins ? », et ce chiffre n'existait pas.

Le bras « nom + description » du banc en etait un substitut, et un mauvais : il
fait tourner le moteur de GK sur deux champs, alors que gb est un modele de
langage qui lit 186 descriptions d'un coup. **Comparer GK a une version
appauvrie de lui-meme ne dit rien de gb**, et son 0,401 a servi de reference
pendant deux jours.

Les deux donnees sont pourtant deja en base, sans aucune injection :
`requests` porte la demande (`pre_llm_call`), `skill_uses` porte le skill que gb
a reellement charge (`on_skill_lifecycle`), et l'echantillon annote porte le bon
skill. `gk confronter` les croise.

Trois honnetetes, parce que la conclusion porte une decision :

* **l'appariement reste une correlation** -- les deux hooks ne partagent aucune
  clef, donc on rapproche par session et par temps ;
* **deux fenetres, et la plus genereuse pour gb.** La fenetre courte apres la
  demande a deja rendu zero : gb charge surtout en debut de session. La mesure
  de session le credite d'un chargement **meme anterieur** a la demande. Elle
  l'avantage deliberement : **si gb perd sous une regle qui l'avantage, la
  conclusion tient** ;
* **l'abstention n'est pas une erreur.** Une session ou gb n'a rien charge est
  comptee a part : confondre « gb s'est trompe » et « gb n'a rien charge »
  rendrait le chiffre inutilisable, les deux ayant des remedes opposes.

**Et une coincidence a ne pas prendre pour un resultat** : 36 % des demandes
n'ont aucun skill qui convienne, et 21 sessions sur 63 (33 %) n'ont charge aucun
skill. L'explication la plus economique est que ce sont **les memes demandes**,
donc que gb decline deja correctement. Si elle est juste, la marge restante de
GK n'est pas dans les abstentions mais dans les cas ou gb charge **le mauvais**
skill -- et c'est exactement ce que `gk confronter` mesure.

### Ce que le resultat decidera

| Si | Alors |
|---|---|
| gb est nettement **sous** GK (ecart > 0,20) | la phase 3 a un fondement, et le brief se concoit sur les cas ou gb se trompe |
| les deux sont **proches** | GK ne sert pas de brief. Il reste un outil de diagnostic de la bibliotheque, ce qu'il fait deja tres bien |
| gb est nettement **au-dessus** | le projet change de but, et c'est a dire franchement |

A n = 30, un ecart de quelques points ne signifie rien ; un ecart de vingt, si.
C'est la seule conclusion que ce corpus autorise, et c'est assez pour trancher.

## Le resultat de la confrontation : le brief est mort

Releve du 2026-10-23, sur les 30 demandes reelles ou un humain a designe le bon
skill. **C'est la mesure qui decidait de la phase 3, et elle tranche contre
elle.**

| | GK (1 proposition) | gb (9,1 skills charges par session) |
|---|---|---|
| Bon skill trouve | 12 / 30 = **0,400** | 10 / 30 = **0,333** |

L'ecart de +0,067 **n'est pas un resultat**. La table de contingence le montre :

| | gb juste | gb faux |
|---|---|---|
| **GK juste** | 3 | 9 |
| **GK faux** | 7 | 11 |

Neuf paires discordantes pour GK, sept pour gb : **McNemar exact, p = 0,80**.
Autant dire une piece lancee en l'air. L'annonce « GK fait mieux que gb » ne
tient pas une seconde d'examen, et c'etait annonce d'avance -- « un ecart de
quelques points ne signifie rien a n = 30 ».

### Le biais structurel, et il va dans notre sens

**La verite terrain vient de la liste de GK.** L'annotateur choisissait parmi
les cinq propositions de GK ; si le bon skill n'y figurait pas, il repondait
`0` et le cas sort de la mesure. Donc `attendu` est **toujours un des cinq de
GK, par construction**, et gb est juge sur le terrain de GK.

Consequence : si gb avait charge le bon skill dans un cas que GK ne proposait
pas, ce cas est exclu et gb est penalise **invisiblement**. Le 0,333 de gb est
donc un **plancher**, et le 0,400 de GK est mesure chez lui. La comparaison est
biaisee en faveur de GK.

C'est ce qui rend la suite solide : **elle tient contre le biais.**

### La ou gb engage, gb gagne

En separant selon que gb a charge quelque chose ou non :

| | cas | GK | gb |
|---|---|---|---|
| **gb a charge au moins un skill** | 23 | 6 / 23 = **0,261** | 10 / 23 = **0,435** |
| **gb n'a rien charge** | 7 | 6 / 7 = **0,857** | 0 par definition |

**Quand gb choisit, il choisit mieux que GK** -- 0,435 contre 0,261, et ce
malgre un biais qui avantage GK. Sept paires discordantes contre trois
(p = 0,34, donc non demontre, mais la direction est contre GK).

C'est le troisieme scenario annonce avant la mesure : « gb est nettement
au-dessus, le projet change de but, et c'est a dire franchement. » Le voici dit.

**Injecter un brief qui designe un skill reviendrait donc a remplacer un
jugement meilleur par un jugement moins bon.** Ce n'est plus « non faute de
preuve », c'est **non parce que la mesure dit le contraire**, dans le seul
endroit ou une comparaison etait possible.

### Ce qui survit, et qui n'etait pas le projet

**Un : les abstentions.** Sur les 7 demandes ou gb n'a rien charge, GK nomme le
bon skill 6 fois. Ce sont des occasions manquees nettes, et elles **refutent**
l'hypothese du tour precedent -- j'avais suppose que les 36 % de demandes sans
reponse et les 21 sessions sans chargement etaient le meme phenomene, donc que
gb declinait a raison. Faux : il declinait a tort au moins six fois.

Mais l'IC95 va de 0,49 a 0,97, et surtout **les 7 cas ne couvrent que 4 skills
distincts**, dont `cartes-executeur` a lui seul en porte 4. L'effectif reel est
donc d'environ quatre situations. **Encourageant, insuffisant pour construire.**
Ce qui le trancherait est un echantillon **cible** sur les sessions sans
chargement -- vingt minutes d'annotation sur le seul lot qui compte, au lieu de
cinquante demandes au hasard.

**Deux : gb charge neuf skills par session.** Moyenne 9,1, maximum 13, et
**209 chargements pour 10 bonnes cibles** sur les 23 cas. Personne ne regardait
ce chiffre, il ne depend d'aucune verite terrain, et il change la question.

| GK, sur les memes 30 cas | |
|---|---|
| rappel@1 | 0,400 |
| **rappel@2** | **0,700** |
| rappel@3 | 0,833 |
| rappel@4 | **1,000** |

Le bon skill est dans les **deux premiers** de GK sept fois sur dix, et dans les
quatre premiers **toujours**. En face, gb charge neuf skills pour le trouver une
fois sur trois.

Donc la valeur de GK n'est pas « trouver le bon skill » -- gb le fait mieux --
mais **« en charger deux au lieu de neuf »**. C'est un probleme de budget de
contexte, pas de pertinence, et c'est un autre projet : il se mesure en jetons
economises et en qualite de reponse, pas en rappel@1.

**Trois : le diagnostic de la bibliotheque.** `gk manques`, `gk confusions`, les
36 % de demandes sans reponse, les 31 contre-indications sur 186, la couverture
des declencheurs passee de 36 % a 78 %. Ca marche, ca rend du travail
actionnable, et c'est deja livre.

### Une affirmation a retirer

Le bras « nom + description » du banc (0,401) a servi deux jours de reference
pour gb. Le vrai chiffre de gb est 0,333, mesure autrement et sur un autre
corpus : **la proximite est une coincidence** et ne valide pas le substitut. Un
moteur de mots sur deux champs n'approxime pas un modele de langage qui lit 186
descriptions, meme quand les nombres se ressemblent.

## Choix contre choix : GK gagne, et la conclusion precedente est retiree

> **RETIRE le 2026-10-23 apres audit.** Le « GK gagne trois fois mieux » de
> cette section comparait GK a la courbe **par tour** de gb, qui sous-estime gb
> de 5 cas : il avait deja le bon skill charge avant la demande. La mesure juste
> est 0,400 contre **0,300**, et elle ne demontre rien. Voir
> « L'audit de la confrontation » plus bas. La section est conservee parce que
> l'erreur et sa correction font partie du dossier.

Releve du 2026-10-23, apres correction du denominateur. 30 demandes reelles,
verite terrain humaine, **le rang du bon skill chez l'un et chez l'autre**.

| Rang | GK | gb (sa sequence du tour) |
|---|---|---|
| **1** | **0,400** (12/30) | **0,133** (4/30) |
| **2** | **0,700** (21/30) | 0,167 (5/30) |
| 3 | 0,833 | 0,167 |
| 5 | **1,000** | 0,167 |

**Trois fois mieux au rang 1, et la courbe de gb est plate** : passe le premier
chargement, il n'en fait presque jamais un second qui rattrape.

### Ce que je retire

La conclusion du releve precedent -- « la ou gb engage, gb gagne, 0,435 contre
0,261 » -- **est retiree**. Elle etait fausse pour deux raisons cumulees :

* elle comparait le **rang 1 unique** de GK au **lot de 9,1 skills** que gb
  charge dans une session. « Le bon skill est parmi les neuf charges » mesure un
  ratissage, pas une decision. gb ne choisit pas un skill, il en charge neuf ;
* elle conditionnait sur un **sous-ensemble choisi apres coup** (les 23 cas ou
  gb avait charge quelque chose), ce qui est exactement l'erreur deja commise et
  consignee sur la ponderation par population.

Le but de GK est de **choisir**. La mesure devait donc comparer deux choix, et
c'est elle qui compte.

### Les trois lectures de gb, et ce qu'elles encadrent

| Mesure de gb | Valeur | Ce qu'elle suppose |
|---|---|---|
| Rang 1, sa sequence du tour | 0,133 | que gb recharge quand il a besoin |
| Tous rangs, sa sequence du tour | 0,167 | idem |
| Session entiere, jusqu'a 13 skills | **0,333** | gb credite de tout, meme anterieur |

**gb n'a rien charge apres 14 des 30 demandes (47 %)**, dont 7 dans des sessions
sans aucun chargement. Les 7 autres sont des demandes ou gb avait deja charge
plus tot : il pouvait donc avoir le bon skill en contexte **sans nouvel
evenement**. C'est pourquoi la mesure de session existe comme borne haute.

**La vraie precision par demande de gb est donc entre 0,167 et 0,333.** Et GK
est a 0,400 au rang 1, 0,700 au rang 2.

| Comparaison | GK | gb | Lecture |
|---|---|---|---|
| Rang 1 contre rang 1 | 0,400 | 0,133 | **GK gagne, p entre 0,02 et 0,08** |
| Rang 1 contre borne haute de gb | 0,400 | 0,333 | egalite (p = 0,80) |
| **Rang 2 contre borne haute de gb** | **0,700** | 0,333 | **le double, p entre 0,001 et 0,06** |

A n = 30 rien n'est demontre au seuil de 5 % de facon robuste, et il faut le
dire. Mais la direction est constante sur les trois lectures, et l'amplitude est
grande : ce n'est plus le +0,067 de tout a l'heure.

### Le biais reste, et il est le dernier point faible

**La verite terrain vient des cinq propositions de GK.** Un cas ou gb aurait
charge le bon skill que GK ne proposait pas est sorti de la mesure avec la
reponse `0`. gb est donc juge chez GK, et ses chiffres sont des planchers.

Ce biais se mesure, et pour une fois la question posee a l'humain est **moins
chere** que la precedente : sur les 17 cas `0`, gb a charge quelque chose ;
« l'un de ces skills sert-il la demande, oui ou non ? » est un oui/non sur un
nom deja ecrit, pas une recherche dans 186. Dix minutes, et cela resout **a la
fois** le biais et `x` -- un skill de gb juge bon sur un cas `0` est precisement
un bon skill hors des cinq de GK.

### Ce qui bloque encore la phase 3, et ce n'est plus la pertinence

Deux constats se combinent :

* **GK choisit mieux que gb.** C'est etabli, avec les reserves ci-dessus ;
* **36 % des demandes n'ont aucune reponse**, et GK n'a aujourd'hui aucun moyen
  fiable de se taire : le seuil ajuste sur les phrases citees ne laisse passer
  que 7,7 % des demandes reelles.

Un brief qui parle sur 100 % des demandes se trompe donc par construction sur au
moins 36 % d'entre elles, quelle que soit la qualite du classement. **Le blocage
a change de nature** : ce n'est plus « GK choisit-il bien ? » mais « GK sait-il
se taire ? »

Et pour la premiere fois le corpus pour le regler existe : **47 demandes reelles
etiquetees, dont 17 sans reponse**. Le seuil se refixe la-dessus, avec moitie
d'ajustement et moitie de verification comme la premiere fois -- la precaution
qui avait deja attrape un surajustement de 1,0 a 0,333.

### La forme que le brief doit prendre, si brief il y a

Deux decisions decoulent directement des chiffres :

* **deux entrees, pas une.** 0,700 contre 0,400 est le plus gros gain
  disponible, et il coute une ligne. Au rang 3 on gagne encore 0,133 pour une
  ligne de plus, au rang 4 le reste ;
* **le droit de ne rien dire**, sur un seuil ajuste sur les demandes reelles.
  Sans lui, le brief est faux 36 fois sur 100 par construction.

## L'audit de la confrontation, et l'etat verifie

Demande de GM : « je veux que tu verifies avant tout. » Legitime -- j'avais
change de conclusion deux fois sur la meme mesure. Quatre points faibles,
trois verifiables depuis la base.

### Ce que l'audit a trouve

**Un defaut reel dans l'instrument.** `skill_uses` porte 765 `loaded`, mais
aussi 206 `patched`, 10 `edited`, 4 `created` et 8 `stale` : **228 evenements
sur 993 ne sont pas des usages**, ce sont des evenements d'ecriture de skill --
un collegue qui corrige un fichier, pas gb qui le lit. Je les comptais comme des
choix de gb. Pire que de gonfler son score : un skill fraichement corrige
pouvait occuper le rang 1 de sa sequence et repousser son vrai choix d'un cran.

**Et le resultat y resiste.** Apres filtrage, `gb_a_choisi_quelque_chose` passe
de 16 a 15 et **tous les autres chiffres sont identiques** : les evenements
ecartes portaient sur des skills qui n'etaient pas ceux attendus. C'est le
meilleur resultat possible d'une verification -- le chiffre tient malgre un vrai
defaut. A consigner comme tel, parce que l'inverse serait arrive une fois sur
deux.

**Les reutilisations sont visibles**, et c'etait le risque principal :
`use_count` monte a 524, et 736 evenements sur 993 portent un compteur
superieur a 1. La courbe par tour de gb est donc une mesure, pas un artefact de
l'instrument.

**gb etait bien sous-estime, de 5 cas.** Il avait deja le bon skill charge avant
la demande 5 fois. En le creditant du tour **ou** d'un chargement anterieur, il
passe de 0,133 a **0,300**. Plus bas que la mesure de session (0,333) : un cas
avait son chargement apres la demande suivante, donc il repondait a celle-la.

### L'etat verifie, et il ne demontre rien

| | GK | gb |
|---|---|---|
| **Mesure la plus juste par demande** | **0,400** (12/30) | **0,300** (9/30) |
| Rang 2 | 0,700 | 0,167 (sequence) / 0,333 (session) |

Trois cas d'ecart. **McNemar p entre 0,61 et 0,66**, et l'effectif reel n'est pas
30 : **13 sessions distinctes**, 18 skills attendus distincts. Un ecart de trois
cas sur treize observations independantes ne demontre rien, dans aucun sens.

**Troisieme position sur cette mesure, et c'est celle qui reste** quand on
retire mes trois erreurs : un denominateur faux (rang 1 contre un lot de neuf),
une courbe qui sous-estimait gb de 5 cas, et 23 % d'evenements qui n'etaient pas
des usages.

### Le biais de selection, qui ne se verifie pas depuis la base

La verite terrain vient des cinq propositions de GK, donc les 30 cas comparables
sont **ceux ou GK a reussi son rappel@5**. GK est juge sur un sous-ensemble que
sa propre reussite a selectionne ; gb n'a pas cet avantage. C'est enonce a
chaque sortie de la commande, pour qu'il ne disparaisse plus du raisonnement
comme il l'a fait deux fois.

### Ce que l'audit change pour la decision

Il la deplace, et dans une direction qui n'avait pas ete vue.

**J'ai cherche a justifier la phase 3 par des mesures hors production, alors que
la phase 3 est elle-meme une experience randomisee** -- un tour sur deux avec le
brief, les deux moitieés comparees sur la meme periode. Cette mesure repond a la
question mieux que n'importe quel substitut hors ligne, sans annotation, sur de
vraies demandes, et c'etait la conception d'origine.

Aucun chiffre hors ligne ne tranchera a 13 sessions. Il en faudrait deux ou
trois heures d'annotation de plus, ou l'A/B.

**Et le cout de se tromper est borne**, parce que la conception d'origine le
bornait deja : gb garde le catalogue complet au prompt systeme, donc le brief
informe sans contraindre, et gb conserve le dernier mot. Le risque n'est pas
qu'un mauvais choix de GK ecrase un bon choix de gb, c'est **l'ancrage** -- et il
s'observe des les premiers jours par le compteur de corrections de la phase 1,
qui existe depuis le debut et n'a jamais servi.

### Les trois conditions que les chiffres dictent

| Condition | Pourquoi | Etat |
|---|---|---|
| **Deux entrees, pas une** | 0,700 contre 0,400, pour une ligne de plus | a faire |
| **Le droit de se taire** | 36 % des demandes n'ont aucune reponse ; un brief qui parle toujours est faux 36 fois sur 100 par construction | **le seul vrai reste** |
| **Un tour sur deux** | c'est la mesure, pas le risque | concu depuis l'etape 8 |

Et une surveillance a ajouter, parce que le risque a ete identifie et qu'aucune
mesure ne le couvre : **la part des sessions qui ne chargent aucun skill, par
bras**. Si le brief detruit des abstentions correctes, elle bougera.

Le seuil se refixe sur les **47 demandes reelles etiquetees, dont 17 sans
reponse** -- la premiere verite terrain du projet sur de vraies demandes. Avec
moitie d'ajustement et moitie de verification, la precaution qui avait deja
attrape un surajustement de 1,0 a 0,333.

## Le reetalonnage : ce que la fuite gonflait vraiment

Releve du 2026-10-23, apres correction de la fuite de contexte. Les 30
etiquettes de l'echantillon rejouees contre un classement refait, ancre sur
chaque demande.

| Rang | Avant (avec fuite) | Apres | Ecart |
|---|---|---|---|
| 1 | 0,400 | **0,433** | **+0,033** |
| 2 | 0,700 | 0,600 | −0,100 |
| 3 | 0,833 | 0,700 | −0,133 |
| 5 | **1,000** | **0,900** | −0,100 |

**La fuite ne gonflait pas le rang 1.** Elle gonflait les rangs intermediaires
et le rappel@5. C'est contre-intuitif et ca s'explique : les tours futurs
tiraient le bon skill **dans la liste** sans le hisser en tete. Le rang 1 gagne
meme un cas en s'en passant.

A retenir pour la suite : une fuite de contexte ne se detecte pas en regardant
l'indicateur principal. Celui-la n'avait pas bouge.

### Le rappel@5 devient une vraie mesure

Avant, il valait 1,000 **par construction** : l'annotateur ne pouvait designer
qu'un des cinq montres. Rejoue contre un classement neuf, **3 etiquettes sur 30
tombent hors des cinq** : 0,900 est le premier rappel@5 du projet qui soit un
resultat et non une tautologie.

**Avec une reserve qui ne disparait pas** : les 30 etiquettes ont ete tirees des
cinq propositions de l'ancien classement. Mesurer le nouveau classement sur
elles est donc autant une mesure de **stabilite** que de rappel, et le biais de
selection reste oriente en faveur de GK.

### La distribution dit ou le classement se perd

| Rang | 1 | 2 | 3 | 4 | 5 | hors |
|---|---|---|---|---|---|---|
| Cas | **13** | 5 | 3 | **6** | 0 | 3 |

**Une bosse au rang 4**, plus haute qu'aux rangs 2 et 3. Un classement sain
decroit ; celui-la ne decroit pas au milieu. Six etiquettes placees quatriemes
ne sont pas « presque trouvees », elles sont mal ordonnees -- et le rang 1 parmi
les 27 retrouves ne vaut que **0,481**.

C'est coherent avec les 67 % d'echecs de redaction : le texte fait entrer un
skill dans la liste, il ne le hisse pas.

### L'etat consolide de la comparaison

| | GK | gb |
|---|---|---|
| Rang 1, mesure la plus juste | **0,433** (13/30) | **0,300** (9/30) |

Quatre cas d'ecart, **McNemar p entre 0,45 et 0,52**, 13 sessions distinctes.
Toujours pas demontre -- mais **la direction a survecu a la correction**, ce qui
n'etait pas acquis.

### Un chiffre qui demande verification

`injections_ecartees: 0`, alors que gb en comptait **53 sur 196**. Deux lectures
possibles : l'echantillon est anterieur a ces injections, ou **les motifs que
j'ai ecrits ne correspondent pas aux bannieres reelles**. La seconde est la plus
probable, puisque je les ai devines depuis un rapport et non depuis les donnees.

`gk demandes --injections 10` tranche : s'il ne rend rien sur les 261 demandes,
le filtre ne filtre rien et les 36 % restent pollues. **A verifier avant de
refixer le seuil**, puisque le seuil se calibre precisement sur la part des
demandes sans reponse.

## Points ouverts

Dernière revue : 2026-10-22, après la mise en service partielle de la phase 1
et l'allègement de la phase 2.

### Décisions à prendre

| Sujet | État |
|---|---|
| **Licence** | **Non tranché, et bloquant avant publication.** Le fichier a été supprimé au commit `3b94d02` et le dépôt est destiné à devenir public |
| **Description GitHub du dépôt** | Annonce encore « FastMCP » et une recherche sémantique, deux choses désormais fausses. Se change dans les réglages du dépôt, hors de portée d'un commit |
| **Seuil d'acceptation de la v1** | Reporté à dessein (étape 8) : il se fixe devant le chiffre du banc, pas avant |
| **Volume de la phase de référence** | « 3 semaines » avait été retenu avant l'A/B. Avec l'A/B, la bonne unité est un nombre de tours par bras, pas une durée. À reprendre |
| **`skills_gate.py` : la phase 3 existe déjà en production** | Voir la section dédiée ci-dessous. Reste à décider si GK le remplace, le complète, ou se recentre |
| **Activation d'un plugin** | Résolu, mais il faut le noter : un plugin n'est chargé que s'il figure dans `plugins.enabled`. Copier le dossier ne suffit pas, et `plugin.yaml.inactif` est un second mécanisme observé dont le statut reste flou |
| **Retirer le catalogue du prompt système** | Envisagé par GM « dans un second temps ». Ce catalogue est aujourd'hui le **filet de sécurité** qui rend GK inoffensif : si GK se trompe, se tait ou tombe, gb voit toujours la liste complète de ses skills. Le retirer fait de GK **le seul chemin** par lequel gb apprend qu'un skill existe — un skill absent du brief devient invisible, et une panne de GK devient une panne de gb. Le gain est mince : un bloc stable est mis en cache, donc il coûte de la fenêtre de contexte (~7 000 jetons) et non des jetons par tour, là où un brief ciblant chaque tour n'est jamais mis en cache. Effet de bord : **sans catalogue, l'A/B n'a plus de bras témoin** où l'agent voit la liste. Arbitrage légitime, mais à prendre après la mesure, et en sachant qu'il change la nature du projet |
| **Le catalogue du prompt système est-il complet ?** | Non résolu. `cli_info_mixin.py:1018` atteste son existence et sa forme (`    - name: description`), mais le code qui le construit porte un autre nom et n'a pas été trouvé. Tronqué ou limité à un profil = un manque réel et identifié ; complet = le problème est le format, pas l'information. C'est la seule inconnue qui puisse rouvrir la phase 2 |
| **Les quatre plugins adjacents** | `gm-routing`, `gm-provenance`, `gm-discord-cards` et `gm-skill-dup-guard` (inactif) touchent le périmètre de GK. Le recouvrement a été constaté, jamais arbitré — notamment entre le garde anti-doublons et le signalement d'orphelins de GK |
| **Les 329 SKILL.md hors bibliothèque** | GK les signalera. Décider s'il faut les ranger, et par qui, reste humain |
| Embeddings | Écartés de la v1. À rouvrir en v2 seulement si le banc l'exige |
| Auto-hébergement d'un modèle | Redevenu discutable depuis qu'AVX2 est confirmé présent, et que Hermes est plafonné à 2 des 4 vCPU. Reporté en v2 |
| Poids BM25 et découpage | Paramètres à régler au banc, phase 2 |

### Inconnues techniques

| Sujet | État |
|---|---|
| **Un plugin qui lève à l'import bloque-t-il le démarrage de gb ?** | Jamais vérifié. C'est le contrôle le plus important de l'étape 7 de la mise en service : si gb ne redémarre pas, désactiver le pont répond à la question |
| ~~Par où arrive le `PluginContext`~~ | **Clos.** Point d'entrée `register(ctx)`, et `ctx.register_hook(nom, callback)` (`plugins.py:916`), confirmé par le `register()` de `gm-routing`. **Déclarer les hooks dans `plugin.yaml` ne câble rien** : `gm-routing` en déclare deux au manifeste et en enregistre cinq dans le code |
| **Trois mécanismes sur `pre_llm_call`** | `gm-routing` l'enregistre, et le hook shell `skills_gate.py` y est branché. GK serait le **troisième**. Les contextes se concatènent, donc l'exigence « brief court » devient plus importante, pas moins : trois blocs s'ajoutent au message avant que l'agent lise sa demande |
| ~~Sous quel nom la réponse arrive à `transform_llm_output`~~ | **Clos, relevé en production le 2026-10-22.** Kwargs réels : `{model, platform, response_text, session_id, telemetry_schema_version, turn_id}`. C'est **`response_text`**, qui ne figurait dans aucune des sept hypothèses |
| **`turn_id` est fourni par les hooks** | Gain non prévu. L'étape 3 se rabattait sur « la correction en attente la plus récente de la session » faute de corrélation ; `turn_id` la rend exacte, ce qui conditionne la classification entre mauvaise recommandation et brief ignoré. Ajouté au schéma par la migration 2, additive |
| Clés exactes de `_mcp_envelope` | Le retour est un dictionnaire ; ses clés se lisent dans la même source. Sans conséquence en phase 1, qui n'utilise aucun retour |
| URL des docs Hermes | `hermes-agent.nousresearch.com` ne résout pas depuis l'environnement de conception. La source dans l'image a servi de substitut, et s'est révélée plus fiable |
| Version de Hermes | Le compose épingle `v2026.9.21` ; le handoff mesurait `v2026.7.20`. Les mesures d'environnement du handoff sont périmées |
| Code du prototype | Absent du dépôt. Les 45 tests et les 86 % de Hit@1 du handoff ne sont pas vérifiables |

### Dettes de la phase 1

| Sujet | État |
|---|---|
| Rechargement à chaud des motifs | La conception l'exige, le code ne le fait pas. `motifs.yaml` est monté depuis l'hôte et modifiable, mais il faut redémarrer le conteneur. `gk patterns` dit ce qui est chargé |
| Webhook Discord | Décidé à l'étape 4, non implémenté. Phase 4 |
| Second point d'entrée `/mcp/agent` | Conçu à l'étape 5, non implémenté : il n'expose que des outils des phases 3 et 4 |

### Clos depuis la dernière revue

AVX2 est **présent**. L'écriture GitHub est **accordée**. Les plugins vivent en
`/opt/data/plugins/`, la config en `/opt/data/config.yaml`, les skills en
`/opt/data/skills` — 537 d'entre eux, la prémisse est confirmée. La convention
de nommage des hooks est `on_<hook>`. Les serveurs MCP se déclarent sous
`mcp_servers:` à plat, avec interpolation de variables d'environnement. Le jeu
de tâches du banc n'est plus bloquant : le corpus se constitue depuis la phase
de mesure.
