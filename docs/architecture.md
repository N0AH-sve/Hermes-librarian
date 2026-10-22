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

## skills_gate.py : la phase 3 existe déjà

Relevé le 2026-10-22 dans `hooks.pre_llm_call` du `config.yaml` de l'agent :
`/opt/data/hooks/skills_gate.py`, 350 lignes, un hook shell en production.

C'est une implémentation de ce que la conception prévoyait pour la phase 3.

| Décision de conception | Ce que `skills_gate.py` fait déjà |
|---|---|
| Injection automatique avant que l'agent réfléchisse | fait, sur `pre_llm_call` |
| Brief accolé au message utilisateur | fait — et **jamais dans le prompt système, pour ne pas invalider le cache de prompt** |
| Ignorer les messages triviaux | fait : `EXEMPT_PATTERNS` couvre politesses et questions d'état mesurable |
| Ne jamais bloquer l'agent | fait : « toute exception => sortie vide + exit 0 » |
| Rappeler les skills pertinents avant rédaction | fait, par mots-clés d'action et de domaine |

Il déduplique par session. Son contrat est `{session_id, user_message,
is_first_turn, model, platform}` sur stdin, `{"context": "..."}` sur stdout.

**Une contrainte que la conception ignorait** : le contexte ne doit jamais aller
dans le prompt système, sous peine d'invalider le cache de prompt. Coût réel,
non mentionné jusqu'ici.

### Ce que cela change par phase

**Phase 1 — y gagne.** Elle mesure ce qui injecte, quoi que ce soit. La
référence cesse d'être « l'agent sans aide » pour devenir « l'aide qui existe
déjà », ce qui est la comparaison honnête. La chaîne déployée peut mesurer
`skills_gate.py` dès maintenant.

**Phase 2 — devient plus intéressante.** Le banc comparera deux approches
réelles sur les mêmes tâches : mots-clés écrits à la main contre BM25 sur le
corps complet. Si les mots-clés gagnent, on le saura sans rien déployer.

**Phase 3 — change de nature.** Non plus « ajouter une injection » mais
« remplacer ou compléter celle en place ». Deux mécanismes sur `pre_llm_call`
ajouteraient chacun leur bloc : il faut trancher, pas empiler.

### Ce que GK apporte encore

- **La mesure.** `skills_gate.py` n'a aucun moyen de savoir s'il aide. C'est
  l'apport irremplaçable, et il est déjà déployé.
- **L'entretien.** Une table de mots-clés se maintient à la main à chaque skill
  ajouté ; un index se met à jour depuis le manifeste. À 537 skills et en
  croissance, l'écart se creuse.
- **Les faits sourcés**, absents de `skills_gate.py`.
- **Et peut-être rien sur la recherche elle-même** : si la table de domaines est
  bien faite, BM25 n'apportera pas grand-chose. Le banc le dira, et cette issue
  doit être acceptable.

### À reprendre plutôt qu'à réécrire

`ACTION_PATTERNS` et `EXEMPT_PATTERNS` sont le filtre des messages triviaux de
l'étape 3, déjà écrits en français, avec gestion des accents, éprouvés en
production. GK les reprend.

## Points ouverts

Dernière revue : 2026-10-22, après la mise en service partielle de la phase 1.

### Décisions à prendre

| Sujet | État |
|---|---|
| **Licence** | **Non tranché, et bloquant avant publication.** Le fichier a été supprimé au commit `3b94d02` et le dépôt est destiné à devenir public |
| **Description GitHub du dépôt** | Annonce encore « FastMCP » et une recherche sémantique, deux choses désormais fausses. Se change dans les réglages du dépôt, hors de portée d'un commit |
| **Seuil d'acceptation de la v1** | Reporté à dessein (étape 8) : il se fixe devant le chiffre du banc, pas avant |
| **Volume de la phase de référence** | « 3 semaines » avait été retenu avant l'A/B. Avec l'A/B, la bonne unité est un nombre de tours par bras, pas une durée. À reprendre |
| **`skills_gate.py` : la phase 3 existe déjà en production** | Voir la section dédiée ci-dessous. Reste à décider si GK le remplace, le complète, ou se recentre |
| **Activation d'un plugin** | Résolu, mais il faut le noter : un plugin n'est chargé que s'il figure dans `plugins.enabled`. Copier le dossier ne suffit pas, et `plugin.yaml.inactif` est un second mécanisme observé dont le statut reste flou |
| **Les quatre plugins adjacents** | `gm-routing`, `gm-provenance`, `gm-discord-cards` et `gm-skill-dup-guard` (inactif) touchent le périmètre de GK. Le recouvrement a été constaté, jamais arbitré — notamment entre le garde anti-doublons et le signalement d'orphelins de GK |
| **Les 329 SKILL.md hors bibliothèque** | GK les signalera. Décider s'il faut les ranger, et par qui, reste humain |
| Embeddings | Écartés de la v1. À rouvrir en v2 seulement si le banc l'exige |
| Auto-hébergement d'un modèle | Redevenu discutable depuis qu'AVX2 est confirmé présent, et que Hermes est plafonné à 2 des 4 vCPU. Reporté en v2 |
| Poids BM25 et découpage | Paramètres à régler au banc, phase 2 |

### Inconnues techniques

| Sujet | État |
|---|---|
| **Un plugin qui lève à l'import bloque-t-il le démarrage de gb ?** | Jamais vérifié. C'est le contrôle le plus important de l'étape 7 de la mise en service : si gb ne redémarre pas, désactiver le pont répond à la question |
| Par où arrive le `PluginContext` | `class PluginContext` existe (`hermes_cli/plugins.py:225`) et porte `call_mcp`, mais ce qui le passe au plugin n'a pas été lu. Le pont le cherche à ce qu'il sait faire et journalise ce qui a marché |
| Sous quel nom la réponse arrive à `transform_llm_output` | Idem : plusieurs noms plausibles essayés, le relevé dira lequel |
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
