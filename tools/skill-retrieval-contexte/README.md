# Le contexte de conversation pour `skill-retrieval`

Correctif local pour le plugin tiers
[`skill-retrieval`](https://github.com/Green-Needle-Tech/skill-retrieval) (MIT),
qui ne classe ses skills que sur `user_message` : son hook déclare
`**kwargs` et ne le lit jamais, donc le `conversation_history` que Hermes lui
tend est reçu puis jeté.

Ce correctif n'est pas de GK, et il ne le touche pas. Il vit ici parce que la
boucle de travail du projet est « pousser, puis `git pull` sur le VPS ».

## Pourquoi un repli, et non une requête enrichie en permanence

`BM25Index.retrieve` **déduplique les jetons** de la requête (`seen`). Trois
conséquences qu'on ne voit pas venir :

* un jeton du contexte pèse exactement autant qu'un jeton de la question ;
* répéter un terme ne le pondère pas, donc la méthode de GK
  (`POIDS_CONTEXTE = 0.3`) n'est pas transposable telle quelle ;
* le plancher de pertinence est **relatif** au meilleur score, donc du contexte
  qui fait monter le sommet peut faire tomber sous le plancher un résultat
  légitime.

Le contexte n'est donc consulté que quand la question seule n'a rien décidé :
moins de `CONTEXT_MIN_TOKENS` jetons indexables après normalisation, ou un
résultat vide après le plancher. **Sur un tour où le plugin répondait déjà,
rien ne change** — c'est la propriété que les tests épinglent.

## Procédure

```bash
cd ~/greenknowledge && git pull origin claude/repo-access-2k0hcz
python3 tools/skill-retrieval-contexte/appliquer_contexte.py <dossier-du-plugin>
```

L'applicateur sauvegarde `__init__.py`, copie `context_boost.py` dans
`scripts/`, et insère huit lignes dans `_on_pre_llm_call`. Il s'ancre sur deux
lignes présentes en 0.5 comme en 0.6, jamais sur des numéros de ligne, et il
**refuse** plutôt que de patcher à moitié si une ancre manque.

Les tests, à copier dans le dossier `tests/` du plugin :

```bash
cp tools/skill-retrieval-contexte/test_context_boost.py <dossier-du-plugin>/tests/
cd <dossier-du-plugin> && python3 -m pytest tests -q
```

Puis redémarrer la session Hermes et chercher `Context fallback used` dans ses
logs. Le premier appel journalise aussi la forme réelle de
`conversation_history` (`conversation_history relevé: key=... first=[...]`) :
elle n'est documentée nulle part, et la leçon de `response_text` est qu'il faut
la lire et non la supposer.

## Réglages

| Variable | Défaut | Effet |
|---|---|---|
| `SKILL_RETRIEVAL_CONTEXT` | `1` | `0` désactive tout, sans désinstaller |
| `SKILL_RETRIEVAL_CONTEXT_TURNS` | `2` | nombre de derniers tours lus |
| `SKILL_RETRIEVAL_CONTEXT_CHARS` | `300` | troncature par tour |
| `SKILL_RETRIEVAL_CONTEXT_MIN_TOKENS` | `3` | en dessous, la question est jugée trop pauvre |

Ces variables se posent sur le service `hermes` du compose ou dans son
`env_file` : `plugins.entries.<nom>.env` **n'est pas propagé** par Hermes
v2026.9.21, relevé le 2026-10-23.

## Ce que ce correctif ne fait pas

* **`_is_chatty` est laissé intact.** Un message de trois jetons ou moins qui
  ressemble à un accusé de réception (`ok`, `go`, `thanks`) sort du hook avant
  toute recherche, donc le contexte ne le rattrape pas. C'est une décision
  séparée, qui change le comportement de tous les tours courts.
* **L'index reste bâti sur le frontmatter** — nom ×3, tags ×2, catégorie,
  description. Le contexte ne peut matcher que des métadonnées, jamais le corps
  d'un `SKILL.md`.
* **Il ne mesure rien.** Et `skill-retrieval` compacte le prompt système en noms
  seuls, donc il modifie le gb que GK mesure : les étiquettes prises avant son
  installation et après ne sont pas comparables.
