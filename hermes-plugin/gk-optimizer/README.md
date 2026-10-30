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
inspired it, and the mechanism guarantees it rather than the code: `pre_llm_call`
returns a string that **Hermes appends** to the user message
(`turn_context.py:453`, `plugin_user_context  # appended to user message`).
The plugin therefore returns **the block alone** and has no way to overwrite
anything. Returning the message together with the block would make it appear
twice in the prompt.

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

- **The user's message is never rewritten.** The hook returns the block alone
  and Hermes appends it, so overwriting is not something the plugin could do.
  Several plugins' contributions are collected rather than overwritten, so this
  coexists with `gm-routing` and with the future brief without coordination.
- **Never more than 1.5 seconds of waiting.** The call runs on a thread that is
  abandoned past that: a slow or dead GreenKnowledge yields an unchanged turn,
  never a blocked one. Hermes' floor for `call_mcp` is one second.
- **A circuit breaker.** After three consecutive failures, no call at all for
  sixty seconds. Without it, a stopped server would add its timeout to every
  message of the day.
- **Nothing raises.** Every hook body and the loader are wrapped.
- **Returning `None` is always safe** and is the default on anything unexpected:
  no context, no recognised message, no block, an unparseable reply.

## What is verified, and what is not

Verified against Hermes `v2026.9.21`'s source in an earlier session, and
recorded in `docs/architecture.md`:

- The plugin model is **declare hooks in `plugin.yaml`, implement module
  functions `on_<hook>(**kwargs)`**. There is no `ctx` argument and no
  `register_hook`.
- `pre_llm_call` **returns a string appended to the user message**, and several
  plugins' contributions are **collected**, not overwritten.
- `pre_llm_call` provides `session_id`.
- A hook that raises is **caught and logged, not fatal**. The wrapping here is
  therefore a belt over braces, kept because it is the convention of the
  plugins already installed.
- `call_mcp(server, tool, arguments, timeout)` returns a dict, denies by
  default with `PermissionError`, and bounds `timeout` to 1–600 seconds.

Two unknowns remain, searched among plausible candidates and recorded on the
first call to `/opt/data/gk-optimizer.jsonl`:

- **Where the API object carrying `call_mcp` arrives.** The last unknown of the
  bridge, shared with `gk-bridge`; it is recognised by what it can do.
- **Under which key the user message is passed.** `pre_llm_call`'s payload was
  only read for `session_id` and `is_first_turn`. If the message is not in it at
  all, the optimizer cannot work — and neither can Phase 3, since `prepare`
  needs the task. The log then writes `message_introuvable` with the real kwarg
  names, and that is the line to read after the first message.

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
