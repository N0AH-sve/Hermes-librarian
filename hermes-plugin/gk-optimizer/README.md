# gk-optimizer

The prompt optimizer, Hermes side. Deliberately dumb, like `gk-bridge`: all the
intelligence is in the server, and the plugin is a courier that does not
understand what it carries.

It does two things for a message written in French, in a hurry, with typos:

- **Adds a corrected reading next to it.** Abbreviations expanded, frequent
  misspellings fixed, missing accents restored. The reading is what the skill
  index will be matched against.
- **Stops the turn when the request contradicts itself, or contradicts the
  conversation**, and hands the agent the question to ask instead.

## It never rewrites the user's message

This is the one difference from the Hermes `prompt-optimizer` plugin that
inspired it, and it is deliberate. The block is **appended**; the original text
stays a prefix of what reaches the model.

Three reasons, in order of weight:

1. **A typo may be a proper noun.** `ovh`, `nginx`, a filename, a command.
   Correcting blindly inside the message would destroy the request; alongside
   it, the worst case is a useless line.
2. **The reading is auditable.** The user sees what GK understood. A silent
   rewrite is only noticed once the agent has worked on something else.
3. **The real beneficiary is the index**, not the prose.

## Why it is a separate plugin from gk-bridge

Because it sits on the **input** path of the turn, and `gk-bridge` does not.
The two risks are not alike, and separating them means this one can be cut on
its own: renaming `plugin.yaml` to `plugin.yaml.inactif` — the local convention
— disables the optimizer without stopping the measurement chain, which is what
you want to keep running longest.

## What it guarantees

- **The user's message is never rewritten.** Append only.
- **Never more than 1.5 seconds of waiting.** The call runs on a thread that is
  abandoned past that: a slow or dead GreenKnowledge yields an unchanged turn,
  never a blocked one. Hermes' floor for `call_mcp` is one second.
- **A circuit breaker.** After three consecutive failures, no call at all for
  sixty seconds. Without it, a stopped server would add its timeout to every
  message of the day.
- **Nothing raises.** Every hook body and the loader are wrapped.
- **Returning `None` is always safe** and is the default on anything unexpected:
  no context, no recognised message, no block, an unparseable reply.

## What it discovers rather than assumes

Three unknowns of the Hermes plugin API, searched among plausible candidates
and recorded on the first call to `/opt/data/gk-optimizer.jsonl`:

- **Where the `PluginContext` arrives** — recognised by having `call_mcp`.
- **Under which key the user message is passed** to `pre_llm_call`, or whether
  it arrives inside a `messages` list.
- **What Hermes does with the return value.** Both plausible routes are taken:
  if the message came from a list it is modified in place and the hook returns
  `None`; otherwise the new string is returned. The log says which one ran, and
  that is the file to read after the first real message.

## Install

```bash
cp -r hermes-plugin/gk-optimizer /opt/data/plugins/
```

Then grant it access to the server in `/opt/data/config.yaml`, since plugins
have no MCP access by default — `call_mcp` raises `PermissionError` otherwise:

```yaml
plugins:
  entries:
    gk-optimizer:
      mcp_allowlist: ["gk"]
```

**Installing it changes nothing yet.** The server ships with
`optimizer_share: 0`, so no block is injected until that is raised — while
everything is already measured. Set it to `50` for an A/B by conversation, or
`100` to turn it on everywhere:

```yaml
optimizer_share: 100
```

Tune the rules with `gk optimize "<a request>"`, which reads the live rule files
and injects nothing.

## What the agent sees

```
supprime tous les logs mais garde ceux de septembre

[GK] STOP — n'execute aucune action et pose la question ci-dessous telle quelle, en francais.
     Contradiction dans la demande : « supprime tous les logs » et « garde ceux de septembre ».
     Question a poser : Faut-il supprimer, ou conserver ? Les deux sont demandes dans le meme message.
     Attends la reponse de l'utilisateur avant toute action, outil ou conclusion.
```

The stop is **instructed, not enforced**: no known hook lets a plugin cancel a
turn, and the project's rule is to fail open and never block the agent. The
block therefore carries its instruction in plain text, so it works even if the
system prompt section was never registered. Whether the agent actually asks is
measured, not assumed — `gk stats` counts the stops.
