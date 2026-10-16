# GreenKnowledge

A librarian for [Hermes Agent](https://github.com/NousResearch/hermes-agent). Before every turn, it picks the right skills among hundreds, recalls facts it has already verified, and tells the agent what still needs checking.

> **Status: design closed, implementation not started.** The architecture is recorded in [docs/architecture.md](docs/architecture.md), which is the document of record — where it disagrees with this README or with code, it wins. Not ready for use.

## The problem

An agent with a large skill library tends to fail in two ways.

1. **Poor skill selection.** With hundreds of skills, the agent only sees names and short descriptions. It picks the wrong one, or none, and improvises.
2. **Theorizing instead of verifying.** It answers from assumptions rather than checking with its tools, even when the right tool is available.

Asking the agent to call a "find skill" tool does not fix this: an agent that forgets to verify also forgets to ask. GreenKnowledge therefore runs automatically, before the agent starts reasoning.

The second failure is a secondary goal: the brief keeps its "To verify" lines, but v1 is judged only on skill selection. With two primary goals there is no verdict.

## How it works

A small Hermes plugin sends each user message to the GreenKnowledge server through the `pre_llm_call` hook. The server returns a short brief, which Hermes appends to the message:

```
[GK] Skills: perf-audit (12/14 successes), deploy-history
Known: site hosted on OVH (verified 2026-09-20)
To verify: was there a deploy yesterday? -> deploy-history
Ask first: production or staging?
```

The agent's system prompt carries two rules, served by GreenKnowledge rather than hardcoded in the plugin:

- If the brief has an "Ask first" line, ask that question before doing anything else.
- Do not conclude until every "To verify" item has been checked or explicitly dismissed.

The user's original message is never rewritten. The brief is added next to it.

```
user message
  |
  +-- pre_llm_call --> plugin --MCP/HTTP--> GreenKnowledge
  |                                           - skill index (full SKILL.md)
  |                                           - fact store
  +-- brief appended <----------------------- - correction counter
  v
agent turn
  |
  +-- post_llm_call ------> plugin ---------> observe   (correction detection)
  +-- on_skill_lifecycle -> plugin ---------> report    (what the skill did)
  +-- hourly -------------> plugin ---------> catalog   (skill manifest)
```

The plugin is a courier, not a reader: **it does not understand the brief.** It receives an opaque string and appends it. Any change to the brief's format therefore costs no redeployment on the agent, which matters because the plugin lives in Hermes' persistent volume.

## Components

| Component | Role |
|---|---|
| `server/` | The GreenKnowledge MCP server. Python, streamable HTTP transport, long-lived service. |
| `hermes-plugin/gk-bridge/` | The Hermes plugin. Around fifty lines, standard library only, no state. It is the only part tied to the Hermes plugin API, and it is written to be cheap to rewrite when that API moves. |
| `hermes-plugin/gk-probe/` | A throwaway probe for relieving the plugin API of its unknowns. Delete after reading its log. |
| `bench/` | A benchmark harness to measure skill selection, offline. |

### Two MCP entry points

If the agent and the plugin talk to the same server, the agent sees every tool, including ones it has no business calling. So there are two:

| Entry point | Tools | Caller |
|---|---|---|
| `/mcp/bridge` | `prepare`, `observe`, `catalog` | the plugin only, with its own token |
| `/mcp/agent` | `recall`, `remember`, `report` | the agent, declared in its config |

| Tool | Purpose |
|---|---|
| `prepare(task, conv_id)` | Returns the brief: recommended skills, known facts, items to verify, questions to ask. |
| `observe(reply)` | Receives the agent's reply so corrections can be counted. The full text crosses the wire; only a capped extract is kept. |
| `catalog(manifest)` | Receives the full skill manifest and answers with the bodies it wants. |
| `recall(query)` | Returns known facts with source, date and confidence. |
| `remember(fact, source)` | Stores a fact. |
| `report(skill, outcome, source)` | Records whether a skill worked. `source` is `hook`, `agent` or `human`, never mixed. |

The agent sees three tools, so GreenKnowledge does not recreate the problem it is meant to solve.

## Design choices

- **Index the full skill body, not just the metadata.** The SkillRouter study found that removing the body from the index degrades routing accuracy by 29 to 44 points across retrieval methods ([arXiv:2603.22455](https://arxiv.org/abs/2603.22455)).
- **Keyword search only, in v1.** SQLite FTS5 with BM25 over the full body, French stemming done in Python rather than by a custom tokenizer, chunked by heading so long skills are not penalised. No embeddings: the measurement that justified full-body indexing was reached without them, so they return in v2 only if a benchmark on the real skills demands it. Dropping them also means the whole skill library is never sent to a third party.
- **No LLM on the serving path.** None of the brief's lines need one, so the model only runs in the nightly fact extraction. The serving path is entirely local, around 20 ms, with no external dependency.
- **The catalogue is pushed, not scanned.** The plugin sends a full manifest hourly; the server asks only for bodies whose fingerprint changed. The writer announces, so the reader never races a half-written file, and a missed run delays rather than desynchronises. The server needs no access to the skills directory at all.
- **Nothing is copied.** The index stores paths, fingerprints and an FTS index with no stored content. A skill's body is needed only to index it, then discarded.
- **Fail open.** If the server is down or exceeds its timeout, the message reaches the agent unchanged. GreenKnowledge never blocks the agent. It degrades rather than erroring: a brief it cannot build comes back empty, not as a 500.
- **Skip trivial messages.** Short acknowledgements bypass the pipeline. The rules live in the server, in one hot-reloadable place.
- **A short brief.** A few lines of plain text, to keep the agent's context free for the actual task.
- **Facts are proposed, then confirmed.** The server extracts candidate facts nightly and presents them weekly; an unconfirmed fact never reaches a brief. So the mechanism is safe when nobody reviews the queue — and it is what stops conversation content from reaching the agent's prompt as a fact.
- **Facts age.** Each carries a source and a date. After 30 days it moves from "Known" to "To verify"; after 180 it expires. Ageing needs no periodic job: it is a function of the date, computed when the brief is built.
- **Two speeds.** Serving is synchronous and fast. Tidying runs in the background: extracting candidate facts, expiring stale ones, flagging skills that are never selected, and reporting `SKILL.md` files found outside the indexed root. This complements the Hermes Curator rather than replacing it.

## Deployment

A service added to the agent's own `docker-compose.yml`:

```yaml
gk:
  image: greenknowledge:<pinned-tag>
  ports: ["127.0.0.1:8765:8765"]   # loopback only
  volumes: ["gk-data:/var/lib/gk"] # the database
  mem_limit: 256m
  user: "1000:1000"
  healthcheck:
    test: ["CMD-SHELL", "curl -fsS http://127.0.0.1:8765/health || exit 1"]
```

Published to loopback rather than an internal network, because an agent running with `network_mode: host` cannot resolve a bridge service name. The plugin targets `http://127.0.0.1:8765`.

Grant the bridge access to the server in Hermes' config, since plugins have no MCP access by default:

```yaml
plugins:
  entries:
    gk-bridge:
      mcp_allowlist: ["gk"]
```

Requirements: Python 3.11 or later, and a Hermes version with `pre_llm_call`, `post_llm_call`, `on_skill_lifecycle` and `ctx.call_mcp`.

## Measuring

Do not trust a retrieval layer without measuring it. Two instruments, which answer different questions.

**Offline, the benchmark.** Full-body indexing against metadata only — and metadata only is exactly what the agent sees today, so the gap is what GreenKnowledge adds, with no confounders. The harness reports Hit@1, Hit@3 and MRR.

**In production, an A/B.** The brief is injected on half the turns, and corrections are counted on both arms over the same period, so the skill library, the workload, the model and the detector are identical by construction. A before-and-after comparison over several weeks would be confounded by all four.

A correction is an admission like "you're right, I didn't have the right skill", detected by patterns in a separate editable file, in the server, with no LLM. Each is classified as either a bad recommendation or an ignored brief, because the two have opposite remedies. The automatic count has a blind spot — it depends on the agent admitting fault — so human ratings calibrate it; the two are compared, and a divergence means the detector is wrong.

The benchmark corpus builds itself from the measurement phase: hard cases from detected corrections, labelled by the agent's own admission, and easy cases sampled from turns where a skill worked.

## Security

- The server binds to loopback and publishes no port beyond it. That binding, plus the host firewall, is the boundary.
- Two tokens, one per entry point. They stop the agent from calling bridge tools by accident; they are a guard rail, not a boundary, since an agent holding the host's docker socket can reach the container regardless.
- The fact store may hold sensitive information about your projects and infrastructure. Treat its database file accordingly, and note that only four tables are irreplaceable — the rest rebuilds itself from a manifest.
- Content returned by the server is data for the agent, not instructions. Do not use it to drive approvals or command execution without validation. The fact confirmation queue is the control that keeps conversation content out of the agent's prompt.

## Roadmap

Built in this order, which is not the order of apparent value: the measurement comes first so that the plumbing is validated on a payload that cannot hurt anything, and so that there is a baseline to compare against.

- [ ] **Phase 0** — probe the plugin API: the return shape of `ctx.call_mcp`, and whether a plugin that raises blocks startup
- [ ] **Phase 1** — the measurement chain alone: `observe`, correction patterns, retention, `gk stats`, `gk check`, deployment. No brief injected
- [ ] **Phase 2** — catalogue and retrieval, offline: manifest, FTS5, French stemming, chunking, `gk index`, the benchmark on the real skills
- [ ] **Phase 3** — the brief, A/B: `prepare`, the system prompt section, the brief journal, attribution
- [ ] **Phase 4** — the facts: `remember`, `recall`, the confirmation queue, nightly extraction, the weekly digest
- [ ] Optional reranker based on the SkillRouter models, and embeddings, only if Phase 2 says they are needed

## Related work

GreenKnowledge builds on ideas from these projects:

- [SkillRouter](https://github.com/zhengyanzhao1997/SkillRouter): retrieve-and-rerank skill routing, and the study behind the full-body indexing choice.
- [skill-retrieval-mcp](https://github.com/JayCheng113/skill-retrieval-mcp): semantic skill retrieval as an MCP server.
- [skill-router-mcp](https://github.com/skarborous/skill-router-mcp): a local server running the SkillRouter pipeline.
- skillmux: a local MCP server that routes tasks to skills in a SKILL.md vault.

These tools focus on finding skills. GreenKnowledge combines skill routing with a sourced fact store and an explicit list of things to verify, and injects the result automatically before each turn instead of relying on the agent to call a tool.

## Contributing

Issues and pull requests are welcome. For larger changes, please open an issue first to discuss the approach — and read [docs/architecture.md](docs/architecture.md), which records not only what was decided but why, and what was tried and rejected.
