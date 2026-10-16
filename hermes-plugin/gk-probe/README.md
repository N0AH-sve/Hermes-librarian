# gk-probe

A throwaway probe. It adds no functionality and is meant to be deleted.

It answers one question that blocks implementation: **what does `ctx.call_mcp`
return on this Hermes version?** The architecture document records that the
earlier prototype accepts "three plausible formats" because nobody knew. It also
records the plugin API surface of the deployed version, which matters because the
image moved from `v2026.7.20` to `v2026.9.21` after the design was written.

## What it guarantees

1. **It never raises**, neither at import nor inside the hook. We do not yet know
   whether a plugin that raises at load time prevents the agent from starting, so
   it behaves as if it does.
2. **It injects nothing.** The hook always returns an empty string, so the
   agent's behaviour is unchanged.
3. **It runs once**, then goes inert. No latency added to later turns.

By default it only *introspects* — it reads the signature, docstring and
annotations of `ctx.call_mcp` and friends without calling anything. That alone
may answer the question. Nothing leaves the plugin unless you opt in.

## Install

The local convention, read from the six plugins already in
`/opt/data/plugins/`, is a directory holding a `plugin.yaml` manifest and an
`__init__.py` module, optionally with further `.py` files, a `README.md` and
`test_*.py`. One plugin carries `plugin.yaml.inactif`, so **a plugin is disabled
by renaming its manifest** rather than through a CLI command.

This directory follows that layout, minus the manifest: its schema has not been
read yet, and guessing it is the one thing that fails silently. Copy the shape
from a working one:

```bash
docker compose -f next_hermes/compose.yaml exec hermes bash -lc \
  'cat /opt/data/plugins/gm-routing/plugin.yaml'
```

The entry-point name is also unconfirmed, so the module exposes `register`,
`setup`, `init`, `activate` and `on_load` as aliases of the same function.
Whichever one Hermes calls does the work; the others stay inert.

```bash
cp -r hermes-plugin/gk-probe /opt/data/plugins/
# add plugin.yaml, matching the local schema
```

Send the agent one message, then read the result:

```bash
docker compose -f next_hermes/compose.yaml exec hermes bash -lc \
  'cat /opt/data/gk-probe.jsonl'
```

## Optionally, a real call

To record the actual return shape rather than only the signature, point the probe
at an MCP server already declared in Hermes' config:

```bash
GK_PROBE_MCP_SERVER=<server-name>
GK_PROBE_TOOL=<a-harmless-read-only-tool>
```

Pick a tool that only reads. The probe passes it an empty argument object.

## Remove it

```bash
hermes plugins disable gk-probe
rm -rf /opt/data/plugins/gk-probe /opt/data/gk-probe.jsonl
```

## What it does not answer

Whether a plugin that raises at load time blocks the agent from starting. Finding
that out requires deliberately breaking one, so it belongs in a moment when the
agent can be down briefly — not in this probe, whose whole point is to be safe.
