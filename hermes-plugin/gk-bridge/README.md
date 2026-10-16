# gk-bridge

The bridge between Hermes and GreenKnowledge. Deliberately dumb: all the
intelligence is in the server.

**Phase 1 injects nothing.** There is no `pre_llm_call` hook here, so the
agent's input path is untouched. The bridge observes and forwards, and that is
all — the measurement chain ships before the help, so there is a baseline to
compare against and so the plumbing gets validated on a payload that cannot
break anything.

## Why it is this small

It lives in Hermes' persistent volume, so changing it means touching
production. Two consequences shape it:

- **It does not understand what it carries.** The brief, when phase 3 adds it,
  is an opaque string. Changing the brief's format then costs no redeployment.
- **It is cheap to rewrite.** The plugin API belongs to a fast-moving third
  party. Fifty lines, standard library only, no state: a renamed hook is an
  hour of work rather than a project. That is the real insurance, not
  compatibility.

## What it discovers rather than assumes

Two details of the Hermes API were unknown when it was written, and the bridge
finds them instead of guessing:

- **Where the `PluginContext` arrives.** It is recognised by having a
  `call_mcp` method, whether it comes through hook kwargs or through an
  initialisation call.
- **Under which key the agent's reply is passed** to `transform_llm_output`.
  Several plausible names are tried; the first string present wins.

The first call writes what actually worked to `/opt/data/gk-bridge.jsonl`,
including the real kwarg names. Read that file after the first message.

## Install

```bash
cp -r hermes-plugin/gk-bridge /opt/data/plugins/
```

Then grant it access to the server in `/opt/data/config.yaml`, since plugins
have no MCP access by default — `call_mcp` raises `PermissionError` otherwise:

```yaml
plugins:
  entries:
    gk-bridge:
      mcp_allowlist: ["gk"]
```

Declare the server itself in the same config, pointing at loopback rather than
a service name: the agent runs with `network_mode: host`, so no bridge network
exists to resolve one.

To disable, rename the manifest to `plugin.yaml.inactif`, which is the local
convention.

## What it guarantees

- `on_transform_llm_output` **always returns None**. The hook expects a
  replacement string and the first non-None wins, so returning None is
  strictly inert.
- Calls to the server are **fire and forget**, on a daemon thread. This hook
  sits on the path of the reply to the user, and the result is not used.
- **Nothing raises.** Every hook body and the loader are wrapped. We do not yet
  know whether a plugin that raises at load prevents the agent from starting,
  so it behaves as if it does — which is also the convention of the plugins
  already installed here.
- The timeout is **1 second**, the floor Hermes imposes on `call_mcp`.
