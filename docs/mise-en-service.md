# Mise en service de la phase 1

Procédure à suivre dans l'ordre, sur le VPS. Chaque étape se vérifie avant la
suivante, et chacune se défait.

**Le principe de l'ordre** : GreenKnowledge démarre et se vérifie **seul**,
avant qu'on touche à la configuration de l'agent. Le pont est installé en
dernier. Tout ce qui précède est donc sans risque pour gb : un service qui
tourne et que personne n'appelle ne peut rien casser.

**Ce que la phase 1 fait** : elle observe les réponses de l'agent et compte les
aveux. Elle n'injecte rien — le pont ne déclare aucun hook d'entrée. Le
comportement de gb est strictement inchangé.

**Sur l'utilisateur, et c'est un incident qui l'a appris.** `docker compose
exec` entre dans le conteneur **en root**, pas en 1001 comme le processus de gb.
Toutes les commandes ci-dessous passent donc par `-u 1001`. La seule exception
est un `chown` de reparation, qui a besoin de root.

Pourquoi ce n'est pas un detail : le 2026-10-22, la copie de `catalogue.py` a
cree un fichier `root:root` en mode 666 dans `/opt/data`. gb a refuse son propre
fichier de verrou de passerelle (`ignoring host record ... owned by uid 0
(expected 1001)`), n'a pas fini de demarrer, et **a cesse de repondre** jusqu'a
ce qu'un `chown -R 1001:1001` et un redemarrage le remettent. Les etapes
precedentes n'avaient rien casse parce qu'elles ecrasaient des fichiers
existants, et `cat >` preserve le proprietaire d'un fichier qui existe. La phase
2b est la premiere a creer un fichier neuf.

Verification apres toute copie dans `/opt/data` :

```bash
docker compose -f ~/next_hermes/compose.yaml exec -u 1001 hermes bash -lc \
  'ls -ln /opt/data/plugins/gk-bridge/'
```

Tout doit etre `1001 1001`. Si quelque chose est `0 0`, le reparer **avant** de
redemarrer :

```bash
docker compose -f ~/next_hermes/compose.yaml exec -u 0 hermes bash -lc \
  'chown -R 1001:1001 /opt/data/plugins/gk-bridge /opt/data/gk-bridge.jsonl'
```

**Sur les chemins** : les commandes de GreenKnowledge se lancent depuis
`~/greenknowledge`, celles de l'agent visent `~/next_hermes/compose.yaml` en
chemin absolu. Les deux se mélangent dans la procédure, donc les chemins sont
absolus partout où il y a un doute. Adapte si ton arborescence diffère.

---

## Avant de commencer

```bash
df -h /                    # il faut de la place pour construire l'image
docker system df           # si c'est juste : docker system prune
free -h                    # ~250 Mo de plus seront occupes
```

Le volume de données de l'agent pèse déjà 4,7 Go et le disque était à 92 %.
Vérifie que la place a bien été libérée avant de construire.

---

## 1. Le code sur le VPS

```bash
cd ~
git clone https://github.com/N0AH-sve/Hermes-librarian.git greenknowledge
cd greenknowledge
git log --oneline -1
```

**Vérifier** : le dernier commit correspond à ce qui a été fusionné.

*Revenir en arrière* : `rm -rf ~/greenknowledge`. Rien n'a été touché ailleurs.

---

## 2. Le jeton

Il est écrit directement dans un fichier `.env` à côté du compose, jamais
exporté à la main : un `export` ne survit pas au shell, et le service ne
redémarrerait plus ni depuis une autre session ni après un redémarrage du
serveur.

```bash
cd ~/greenknowledge
printf 'GK_BRIDGE_TOKEN=%s\n' "$(openssl rand -hex 32)" > deploy/.env
chmod 600 deploy/.env
cat deploy/.env
```

Ce fichier est ignoré par git. Le jeton qu'il contient servira **deux fois** :
ici, et dans la configuration de l'agent à l'étape 4. Récupère-le par un
copier-coller depuis la sortie de `cat`, jamais en le retapant.

### Ce que ce jeton achète, exactement

Il faut le savoir avant de le mettre en place, parce que cela change le soin
qu'il mérite.

| Contre | Ce qu'il apporte |
|---|---|
| gb lui-même | **rien** : il détient le socket Docker de l'hôte, donc il peut entrer dans le conteneur de GK et lire son environnement |
| Un autre processus de l'hôte | **rien de plus** que le binding sur `127.0.0.1` |
| L'extérieur | **rien de plus** : le port n'est pas publié au-delà de la boucle locale |

Contre un attaquant, il n'apporte donc rien. Ce qu'il achète est une
**protection contre une erreur de configuration future** : si le port se
retrouve un jour publié sur `0.0.0.0`, par copie d'un autre compose ou par un
`-p 8765:8765` tapé vite, le jeton est la seule chose qui reste debout. Cette
classe d'erreur n'est pas théorique — trois en ont été commises sur ce
déploiement en une journée.

C'est donc un filet contre notre propre maladresse, et c'est à ce titre qu'il
est conservé. Sans jeton, GK démarre quand même et le dit bruyamment au
démarrage, dans `/health` et dans `gk check`.

**La forme qui aurait été meilleure, et pourquoi elle est écartée** : une socket
Unix dans un répertoire monté par les deux conteneurs, où les permissions du
système de fichiers *deviennent* l'authentification — pas de jeton, pas de
synchronisation, et impossible à exposer par accident, une socket Unix ne se
publiant pas sur `0.0.0.0`. Écartée parce que `mcp_servers:` attend une
`url: http://…` et que rien n'indique que Hermes accepte un schéma `unix://`.
À reprendre si cela change.

---

## 3. Démarrer GreenKnowledge, seul

```bash
cd ~/greenknowledge
docker compose -f deploy/compose.gk.yaml up -d --build
docker compose -f deploy/compose.gk.yaml logs --tail 20
```

Compose lit `deploy/.env` tout seul, puisqu'il est à côté du fichier compose.
Rien à exporter.

**Vérifier** — trois choses dans les journaux :

```
GreenKnowledge sur http://127.0.0.1:8765/mcp/bridge — 12 motifs (empreinte 9b36fdff7ebc9a33), jeton exige
```

Si tu lis `jeton ABSENT`, la variable n'a pas été transmise.

```bash
curl -s http://127.0.0.1:8765/health | python3 -m json.tool
```

Attendu : `"ok": true`, `"patterns": 12`, `"jeton_exige": true`, et
`"derniere_observation_il_y_a_s"` absent — personne ne l'a encore appelé, ce qui
est normal à ce stade.

```bash
# Le jeton est-il reellement exige ?
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8765/mcp/bridge
# attendu : 401
```

```bash
# Le port n'est-il bien ouvert que sur la boucle locale ?
ss -ltnp | grep 8765
# attendu : 127.0.0.1:8765, et PAS 0.0.0.0:8765
```

*Revenir en arrière* : `docker compose -f deploy/compose.gk.yaml down`. L'agent
n'a pas été touché.

---

## 4. Déclarer le serveur dans la configuration de l'agent

**Sauvegarde d'abord**, selon la convention déjà en usage dans le volume :

```bash
docker compose -f ~/next_hermes/compose.yaml exec -u 1001 hermes bash -lc \
  'cp /opt/data/config.yaml /opt/data/config.yaml.bak-avant-gk-'$(date +%Y%m%d-%H%M%S)
```

Puis ajouter le serveur MCP. L'adresse est la **boucle locale**, pas un nom de
service : l'agent tourne en `network_mode: host`, donc aucun réseau bridge
n'existe pour résoudre un nom.

Trois insertions sont nécessaires, et elles se font par script plutôt qu'à la
main : le fichier compte plusieurs centaines de lignes, il est en production, et
une erreur d'indentation YAML ne se voit qu'au redémarrage de l'agent.

| Insertion | Sans quoi |
|---|---|
| `mcp_servers.gk` | le pont n'a pas de serveur à appeler |
| `plugins.enabled` | **le plugin n'est jamais chargé** — copier le dossier ne suffit pas |
| `plugins.entries.gk-bridge.mcp_allowlist` | `call_mcp` lève une `PermissionError`, par défaut-refus |

```bash
docker compose -f ~/next_hermes/compose.yaml exec -u 1001 -T hermes python3 - < ~/greenknowledge/deploy/brancher-gk.py
```

Le script est dans le dépôt, donc relisible avant d'être lancé. Il sauvegarde de
façon horodatée, insère après des ancres exactes sans relire le fichier par un
analyseur — ce qui détruirait commentaires et mise en forme —, **valide** le
résultat, et **restaure la sauvegarde** si le YAML ne se relit pas ou si une
valeur n'est pas là où elle doit être. Il est idempotent, et refuse d'agir si
une ancre manque plutôt que de deviner où insérer.

Le jeton n'y apparaît jamais : seule la référence `${GK_BRIDGE_TOKEN}` est
écrite, la valeur restant dans le `.env` de l'étape 2.

**Vérifier** : la sortie doit finir par `VALIDE`. Elle liste les insertions
faites, et dit `deja present` pour celles qui l'étaient.

*Revenir en arrière* : restaurer la sauvegarde horodatée que le script a
affichée, puis redémarrer l'agent.

## 5. Installer le pont

C'est la première étape qui touche réellement l'agent. Elle est aussi la plus
facile à défaire.

```bash
docker compose -f ~/next_hermes/compose.yaml exec -u 1001 hermes bash -lc \
  'ls -la /opt/data/plugins/'
# verifier qu'il n'y a pas deja un gk-bridge

cp -r ~/greenknowledge/hermes-plugin/gk-bridge \
      /home/webmanager/next_hermes/data/plugins/
```

> Le chemin hôte du volume est `./data` à côté du `compose.yaml` de l'agent,
> monté sur `/opt/data` dans le conteneur. Adapte si ton arborescence diffère.

**Puis l'activer**, et c'est ce qui manquait : copier le dossier ne suffit pas.
Un plugin n'est chargé que s'il figure dans la liste `plugins.enabled` du
`config.yaml`.

```yaml
# dans /opt/data/config.yaml, en ajoutant a la liste existante
plugins:
  enabled:
    - gm-discord-cards
    - gm-routing
    - gk-bridge
  entries:
    gk-bridge:
      allow_tool_override: false
      mcp_allowlist: ["gk"]
```

Ne reconstruis pas ces listes : ajoute `gk-bridge` à celle qui existe, sans
toucher aux autres entrées. Un plugin figure soit dans `enabled`, soit dans
`disabled`.

**Vérifier** que le manifeste est bien là et qu'il ne déclare aucun hook
d'entrée :

```bash
docker compose -f ~/next_hermes/compose.yaml exec -u 1001 hermes bash -lc \
  'cat /opt/data/plugins/gk-bridge/plugin.yaml'
# attendu : hooks: transform_llm_output et on_skill_lifecycle, et RIEN d'autre.
# Si "pre_llm_call" apparait, n'installe pas : ce n'est pas la phase 1.
```

*Revenir en arrière* : retirer `gk-bridge` de `plugins.enabled`, ce qui est le
mécanisme officiel. Renommer le manifeste en `plugin.yaml.inactif` est un
second mécanisme observé sur un plugin du serveur, mais c'est la liste qui fait
foi.

---

## 6. Redémarrer l'agent

```bash
docker compose -f ~/next_hermes/compose.yaml restart hermes
sleep 20
docker compose -f ~/next_hermes/compose.yaml logs --tail 40 hermes | grep -i "gk-bridge\|plugin"
```

**Vérifier, et c'est le point le plus important de toute la procédure** : que
l'agent a bien démarré.

```bash
docker compose -f ~/next_hermes/compose.yaml ps
curl -fsS http://127.0.0.1:${API_SERVER_PORT:-8742}/health && echo " agent en vie"
```

On ne sait pas encore si un plugin qui lève une exception au chargement empêche
l'agent de démarrer. Le pont est écrit pour ne jamais lever, mais c'est la seule
hypothèse de la conception qui n'a pas été vérifiée dans le code de Hermes.
**Si l'agent ne démarre pas, désactive le pont** (étape 5, retour en arrière) et
redémarre : tu auras au passage répondu à la question.

---

## 7. Le premier message

Envoie un message quelconque à gb, par le canal habituel. Puis :

```bash
docker compose -f ~/next_hermes/compose.yaml exec -u 1001 hermes bash -lc \
  'cat /opt/data/gk-bridge.jsonl'
```

C'est le relevé que le pont fait de l'API réelle. Trois lignes à y chercher :

| Événement | Ce qu'il dit |
|---|---|
| `ctx_trouve` | par où arrive le `PluginContext` — une des deux inconnues restantes |
| `premier_appel` | les **vrais noms de kwargs** du hook — l'autre inconnue |
| `refuse` | la liste blanche de l'étape 4 n'est pas prise en compte |
| `reponse_introuvable` | la réponse arrive sous un nom que le pont n'essaie pas |

**Renvoie-moi ce fichier.** Il permettra de retirer du pont la recherche à
tâtons et de le figer sur la forme réelle — c'est le dernier morceau de
supposition dans tout le code.

Puis, côté serveur :

```bash
docker compose -f ~/greenknowledge/deploy/compose.gk.yaml exec gk gk check
docker compose -f ~/greenknowledge/deploy/compose.gk.yaml exec gk gk stats
```

`gk check` doit maintenant se taire sur l'observation. S'il dit encore
« aucune observation n'a jamais été reçue », le pont n'appelle pas : regarde
`gk-bridge.jsonl`.

---

## Vérification finale

| Contrôle | Attendu |
|---|---|
| `curl .../health` | `ok: true`, `jeton_exige: true` |
| `ss -ltnp \| grep 8765` | `127.0.0.1` uniquement |
| `POST /mcp/bridge` sans jeton | `401` |
| L'agent répond normalement | aucune latence ajoutée, aucun texte en plus |
| `gk check` | aucun problème |
| `gk stats` | `messages_observes` augmente à chaque échange |
| `gk-bridge.jsonl` | `ctx_trouve` et `premier_appel` présents, pas de `refuse` |

---

## Mettre à jour le pont après un correctif

Le pont est le seul morceau de GreenKnowledge qui vit dans le volume de
l'agent, donc le seul qui ne se met pas à jour en reconstruisant une image.
Quatre commandes, dans cet ordre.

**1. Récupérer le code corrigé.**

```bash
cd ~/greenknowledge \
  && git fetch origin \
  && git checkout claude/repo-access-2k0hcz \
  && git pull --ff-only origin claude/repo-access-2k0hcz
```

**2. Recopier le pont, et vérifier que la copie est bien arrivée.** L'écriture
passe par le conteneur plutôt que par l'hôte : le démon Docker tourne en
rootless, donc l'uid de l'hôte et celui du volume ne coïncident pas et un `cp`
depuis l'hôte peut échouer en « permission non accordée ».

```bash
docker compose -f ~/next_hermes/compose.yaml exec -u 1001 -T hermes bash -lc \
  'cat > /opt/data/plugins/gk-bridge/__init__.py' \
  < ~/greenknowledge/hermes-plugin/gk-bridge/__init__.py

docker compose -f ~/next_hermes/compose.yaml exec -u 1001 hermes bash -lc \
  'grep -c response_text /opt/data/plugins/gk-bridge/__init__.py'
# attendu : au moins 1. Si 0, la copie n'a pas eu lieu, ne redemarre pas.
```

**3. Redémarrer l'agent.** Un plugin n'est relu qu'au démarrage. **Compte deux
à trois minutes** : `stop_grace_period` vaut 45 s et le `start_period` du
healthcheck 120 s. Un `ps` qui montre l'agent absent dans les trente premières
secondes ne veut rien dire.

```bash
docker compose -f ~/next_hermes/compose.yaml restart hermes
docker compose -f ~/next_hermes/compose.yaml ps
```

**4. Envoyer un message à gb, puis lire la mesure.** C'est le message qui
produit l'observation ; sans lui le compteur reste à zéro, et ce n'est pas une
panne.

```bash
docker compose -f ~/greenknowledge/deploy/compose.gk.yaml exec gk gk stats
```

Attendu : `messages_observes` supérieur à zéro, et `schema` à 2. Si le schéma
est à 1, le conteneur de GK est antérieur à la migration `turn_id` et demande
un `up -d --build` sur `~/greenknowledge/deploy/compose.gk.yaml`. Si le compteur
reste à zéro, lire `/opt/data/gk-bridge.jsonl` dans le conteneur de l'agent :
c'est lui qui dit pourquoi.

---

## Mettre en service le facteur (phase 2)

Le « facteur » est le morceau qui pousse le catalogue des skills vers GK. C'est
**le seul moment de toute la phase 2 où gb est touché**. Après celui-là, les
étapes suivantes ne demandent qu'une reconstruction de GK.

Le pont ne gagne **aucun hook** : `pre_llm_call` reste absent, et un test le
vérifie. Le manifeste part sur `transform_llm_output`, déjà câblé, après que
l'agent a répondu, sur un fil détaché, au plus une fois par heure.

**1. Récupérer le code.**

```bash
cd ~/greenknowledge \
  && git fetch origin \
  && git checkout claude/repo-access-2k0hcz \
  && git pull --ff-only origin claude/repo-access-2k0hcz
```

**2. Reconstruire GK**, qui porte maintenant les outils `catalog` et `sections`.
gb n'est pas touché : le pont se dégrade en silence si GK est absent.

```bash
docker compose -f ~/greenknowledge/deploy/compose.gk.yaml up -d --build
docker compose -f ~/greenknowledge/deploy/compose.gk.yaml exec gk gk catalogue
```

Attendu : `skills: 0` et une remarque disant qu'aucun manifeste n'a été reçu.
Si la commande `catalogue` n'existe pas, la reconstruction n'a pas pris.

**3. Recopier les deux fichiers du pont.** Il y en a deux désormais : le
manifeste vit dans `catalogue.py`, pour que la partie qui tourne sur le trajet
d'une réponse reste aussi courte qu'avant.

```bash
for f in __init__.py catalogue.py; do
  docker compose -f ~/next_hermes/compose.yaml exec -u 1001 -T hermes bash -lc \
    "cat > /opt/data/plugins/gk-bridge/$f" \
    < ~/greenknowledge/hermes-plugin/gk-bridge/$f
done

docker compose -f ~/next_hermes/compose.yaml exec -u 1001 hermes bash -lc \
  'ls -la /opt/data/plugins/gk-bridge/ ; \
   grep -c _ronde_catalogue /opt/data/plugins/gk-bridge/__init__.py'
```

Attendu : les deux fichiers présents, et un compte supérieur à zéro. Si c'est
zéro, **ne redémarre pas**.

**4. Redémarrer gb.** Deux à trois minutes, comme toujours
(`stop_grace_period` 45 s, `start_period` 120 s).

```bash
docker compose -f ~/next_hermes/compose.yaml restart hermes
docker compose -f ~/next_hermes/compose.yaml ps
```

**5. Envoyer un message à gb, puis lire.** La première ronde part dès la
première réponse, parce que GK réclame par le canal retour (`catalogue_vide`)
sans attendre l'heure.

```bash
docker compose -f ~/greenknowledge/deploy/compose.gk.yaml exec gk gk catalogue

docker compose -f ~/next_hermes/compose.yaml exec -u 1001 hermes bash -lc \
  'grep ronde_catalogue /opt/data/gk-bridge.jsonl | tail -2'
```

Attendu côté `gk catalogue` : `skills` proche du nombre réel de skills, et
`a_indexer` qui tombe à zéro en une ou deux rondes. Côté journal, une ligne par
ronde avec `manifeste_envoye`, `tranches_envoyees`, `restants` et `duree_s`.

| Dans le journal | Ce que ça dit |
|---|---|
| `catalogue_charge` / `voie` | si `catalogue.py` a été chargé par import relatif ou par chemin |
| `hermes` | si `_find_all_skills` a répondu, et combien de skills il a donnés |
| `filtres` | combien de skills du disque Hermes considère inactifs |
| `parcours: tronque` | le garde-fou a coupé : racine inattendue ou budget dépassé |
| `restants` > 0 | normal à la première ronde, la liste est bornée à 40 par appel |
| `erreur: refuse` | `mcp_allowlist` ne contient pas `gk` |
| `forme_inconnue` | le résultat MCP n'a pas la forme attendue : m'envoyer la ligne |

*Revenir en arrière* : remettre les deux fichiers de la version précédente et
redémarrer, ou retirer `gk-bridge` de `plugins.enabled`. Le catalogue déjà reçu
reste dans la base de GK et ne gêne rien.

---

## Ce qui peut mal tourner, et à quoi ça se voit

| Symptôme | Cause probable | Que faire |
|---|---|---|
| L'agent ne démarre plus | un plugin qui lève bloque le démarrage | désactiver le pont (étape 5), redémarrer. Et me le dire : c'est une réponse qu'on cherchait |
| `refuse` dans le journal du pont | `mcp_allowlist` absent ou mal placé | revoir l'étape 4 |
| `reponse_introuvable` | la réponse arrive sous un autre nom | m'envoyer la ligne : elle contient les vrais noms |
| `gk stats` reste à zéro | le pont ne trouve pas son `PluginContext` | m'envoyer `ctx_trouve`, ou son absence |
| `401` persistant | les deux jetons diffèrent | comparer l'environnement de GK et la config de l'agent |
| Réponses de l'agent plus lentes | ne devrait pas arriver : les appels partent sur un fil détaché | me le dire, c'est un défaut |

---

## Ce que la phase 1 ne fait pas encore

- **Aucun brief n'est injecté.** C'est la phase 3.
- **Les 537 procédures ne sont pas indexées.** C'est la phase 2.
- **Les motifs ne se rechargent pas à chaud** malgré la conception : le fichier
  est monté depuis l'hôte et modifiable, mais il faut redémarrer le conteneur de
  GK pour qu'il soit relu. `gk patterns` dit ce qui est réellement chargé.
- **Aucune synthèse Discord**, aucun travail nocturne. Phase 4.

Pendant les semaines qui viennent, GK accumule la mesure de référence et le
corpus du banc d'essai. C'est tout ce qu'on lui demande.
