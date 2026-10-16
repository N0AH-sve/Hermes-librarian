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

Sans jeton, GK démarre quand même mais accepte tout appel arrivant sur la
boucle locale, et il le dit bruyamment au démarrage, dans `/health` et dans
`gk check`. Ce qu'un jeton vaut ici reste modeste : la vraie frontière est le
binding sur `127.0.0.1` plus le pare-feu. L'agent disposant du socket Docker de
l'hôte, il peut de toute façon entrer dans le conteneur. C'est un garde-fou
contre la maladresse, pas une frontière de sécurité.

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
docker compose -f ~/next_hermes/compose.yaml exec hermes bash -lc \
  'cp /opt/data/config.yaml /opt/data/config.yaml.bak-avant-gk-'$(date +%Y%m%d-%H%M%S)
```

Puis ajouter le serveur MCP. L'adresse est la **boucle locale**, pas un nom de
service : l'agent tourne en `network_mode: host`, donc aucun réseau bridge
n'existe pour résoudre un nom.

```yaml
# dans /opt/data/config.yaml
mcp:
  servers:
    gk:
      url: http://127.0.0.1:8765/mcp/bridge
      headers:
        # Le jeton de l'etape 2, a copier-coller depuis `cat deploy/.env`.
        Authorization: "Bearer LE_JETON_ICI"
```

> La forme exacte de cette section dépend de la version de Hermes. Si elle
> diffère, cale-toi sur la façon dont les serveurs MCP déjà en service sont
> déclarés dans ce fichier — il y en a, puisque `.mcp-discovery.lock` existe.

**Vérifier** : la configuration est toujours lisible.

```bash
docker compose -f ~/next_hermes/compose.yaml exec hermes bash -lc \
  'python3 -c "import yaml,sys; yaml.safe_load(open(\"/opt/data/config.yaml\")); print(\"YAML valide\")"'
```

*Revenir en arrière* : restaurer la sauvegarde, et redémarrer l'agent.

---

## 5. Autoriser le pont à appeler le serveur

Les plugins n'ont **aucun accès MCP par défaut** : `call_mcp` lève une
`PermissionError` tant que le serveur n'est pas dans la liste blanche du plugin.

```yaml
# dans /opt/data/config.yaml
plugins:
  entries:
    gk-bridge:
      mcp_allowlist: ["gk"]
```

*Revenir en arrière* : retirer ces lignes. Le pont n'appellera plus rien et le
journalisera, sans rien casser.

---

## 6. Installer le pont

C'est la première étape qui touche réellement l'agent. Elle est aussi la plus
facile à défaire.

```bash
docker compose -f ~/next_hermes/compose.yaml exec hermes bash -lc \
  'ls -la /opt/data/plugins/'
# verifier qu'il n'y a pas deja un gk-bridge

cp -r ~/greenknowledge/hermes-plugin/gk-bridge \
      /home/webmanager/next_hermes/data/plugins/
```

> Le chemin hôte du volume est `./data` à côté du `compose.yaml` de l'agent,
> monté sur `/opt/data` dans le conteneur. Adapte si ton arborescence diffère.

**Vérifier** que le manifeste est bien là et qu'il ne déclare aucun hook
d'entrée :

```bash
docker compose -f ~/next_hermes/compose.yaml exec hermes bash -lc \
  'cat /opt/data/plugins/gk-bridge/plugin.yaml'
# attendu : hooks: transform_llm_output et on_skill_lifecycle, et RIEN d'autre.
# Si "pre_llm_call" apparait, n'installe pas : ce n'est pas la phase 1.
```

*Revenir en arrière*, et c'est la convention locale :

```bash
docker compose -f ~/next_hermes/compose.yaml exec hermes bash -lc \
  'mv /opt/data/plugins/gk-bridge/plugin.yaml \
      /opt/data/plugins/gk-bridge/plugin.yaml.inactif'
```

---

## 7. Redémarrer l'agent

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
**Si l'agent ne démarre pas, désactive le pont** (étape 6, retour en arrière) et
redémarre : tu auras au passage répondu à la question.

---

## 8. Le premier message

Envoie un message quelconque à gb, par le canal habituel. Puis :

```bash
docker compose -f ~/next_hermes/compose.yaml exec hermes bash -lc \
  'cat /opt/data/gk-bridge.jsonl'
```

C'est le relevé que le pont fait de l'API réelle. Trois lignes à y chercher :

| Événement | Ce qu'il dit |
|---|---|
| `ctx_trouve` | par où arrive le `PluginContext` — une des deux inconnues restantes |
| `premier_appel` | les **vrais noms de kwargs** du hook — l'autre inconnue |
| `refuse` | la liste blanche de l'étape 5 n'est pas prise en compte |
| `reponse_introuvable` | la réponse arrive sous un nom que le pont n'essaie pas |

**Renvoie-moi ce fichier.** Il permettra de retirer du pont la recherche à
tâtons et de le figer sur la forme réelle — c'est le dernier morceau de
supposition dans tout le code.

Puis, côté serveur :

```bash
docker compose -f deploy/compose.gk.yaml exec gk gk check
docker compose -f deploy/compose.gk.yaml exec gk gk stats
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

## Ce qui peut mal tourner, et à quoi ça se voit

| Symptôme | Cause probable | Que faire |
|---|---|---|
| L'agent ne démarre plus | un plugin qui lève bloque le démarrage | désactiver le pont (étape 6), redémarrer. Et me le dire : c'est une réponse qu'on cherchait |
| `refuse` dans le journal du pont | `mcp_allowlist` absent ou mal placé | revoir l'étape 5 |
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
