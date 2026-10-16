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

1. Le montage disque disparaît : GK n'a plus besoin de
   `/home/webmanager/hermes/data` monté en lecture seule.
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

**L'auto-hébergement du modèle est écarté**, et la contrainte « tout par API »
du handoff s'avère bien fondée pour des raisons qu'elle n'énonçait pas. Mesures
du VPS : 4 vCPU QEMU « Virtual CPU version 2.5+ », un modèle générique qui
n'expose probablement pas AVX2 — à confirmer par
`grep -o -m1 -E 'avx512[a-z]*|avx2|avx' /proc/cpuinfo | sort -u`. Sans AVX2,
l'inférence CPU perd un facteur 3 à 10 : une requête courte passerait de 100-300
ms à 0,5-3 s, et l'indexation de 500 skills de quelques minutes à des heures. Il
y aurait une ironie à retirer le LLM du chemin de service pour gagner 4 secondes
et y remettre un modèle local qui en coûte 2. S'y ajoute une charge déjà à 2,2
sur 4 vCPU, soit 1,8 vCPU de marge à partager avec gb.

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
| AVX2 sur le VPS | Non vérifié. Décide si l'auto-hébergement d'un modèle est seulement discutable en v2 |
| Licence | À choisir avant publication. Le fichier a été supprimé au commit 3b94d02 |
| Embeddings | Écartés de la v1 (étape 4). À rouvrir en v2 seulement si la mesure sur les vrais skills l'exige |
| Tokenizer FTS5 pour le français | **Première priorité de l'étape 6** : `porter` applique des règles anglaises, et FTS est désormais le seul moteur de recherche |
