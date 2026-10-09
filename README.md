# GreenKnowledge

A librarian for [Hermes Agent](https://github.com/NousResearch/hermes-agent). Before every turn, it picks the right skills among hundreds, recalls facts it has already verified, and tells the agent what still needs checking.

> Status: early development. The design below is settled; the code is being written. Not ready for production use.

## The problem

An agent with a large skill library tends to fail in two ways.

1. **Poor skill selection.** With hundreds of skills, the agent only sees names and short descriptions. It picks the wrong skill, or none, and improvises.
2. **Theorizing instead of verifying.** The agent answers from assumptions rather than checking with its tools, even when the right tool is available.

Asking the agent to call a "find skill" tool does not fix this: an agent that forgets to verify also forgets to ask. GreenKnowledge therefore runs automatically, before the agent starts reasoning.

## How it works

A small Hermes plugin sends each user message to the GreenKnowledge server. The server returns a short brief, which Hermes appends to the message:

```
[GK] Skills: perf-audit (12/14 successes), deploy-history
Known: site hosted on OVH (verified 2026-09-20)
To verify: was there a deploy yesterday? -> deploy-history
Ask first: production or staging?
```

The agent's system prompt carries two rules:

- If the brief has an "Ask first" line, ask that question before doing anything else.
- Do not conclude until every "To verify" item has been checked or explicitly dismissed.

The user's original message is never rewritten. The brief is added next to it.

```
user message
     |
     v
Hermes --pre_llm_call--> gk-bridge plugin --MCP over HTTP--> GreenKnowledge server
     |                                                         - skill index (full SKILL.md)
     v                                                         - fact store
agent turn + brief                                             - usage feedback
     |
     +--on_skill_lifecycle--> gk-bridge --report--> GreenKnowledge server
```

## Components

| Component | Role |
|---|---|
| `server/` | The GreenKnowledge MCP server. Python, FastMCP, streamable HTTP transport. Runs as a long-lived service. |
| `hermes-plugin/gk-bridge/` | A small Hermes plugin. Calls `prepare` on each turn through the `pre_llm_call` hook and forwards skill usage through `on_skill_lifecycle`. It is the only part tied to the Hermes plugin API. |
| `bench/` | A benchmark harness to measure skill selection before and after. |

### MCP tools

| Tool | Purpose |
|---|---|
| `prepare(task)` | Returns the short brief: recommended skills, known facts, items to verify, questions to ask. |
| `recall(query)` | Returns known facts with source, date and confidence. |
| `remember(fact, source)` | Stores a fact. |
| `report(skill, outcome, note)` | Records whether a skill worked for a task. |

The tool surface is kept deliberately small, so GreenKnowledge does not recreate the problem it is meant to solve.

## Design choices

- **Index the full skill body, not just the metadata.** The SkillRouter study found that removing the skill body from the index degrades routing accuracy by 29 to 44 points across retrieval methods ([arXiv:2603.22455](https://arxiv.org/abs/2603.22455)).
- **Hybrid retrieval.** Keyword search (SQLite FTS5, BM25) and multilingual embeddings, merged by reciprocal rank fusion. Multilingual because requests are often written in a different language than the skills.
- **A separate service over HTTP.** The embedding model stays loaded in memory, several clients can share one server, and Hermes updates do not restart or break it.
- **Fail open.** If the server is down or exceeds its timeout (3 seconds by default), the message reaches the agent unchanged. GreenKnowledge never blocks the agent.
- **Skip trivial messages.** Short acknowledgements such as "thanks" or "continue" bypass the pipeline.
- **A short brief.** A few lines of plain text, to keep the agent's context free for the actual task.
- **Two speeds.** Serving is synchronous and fast. Tidying runs in the background: deduplicating facts, expiring stale ones, and flagging skills that are never selected, which usually points to a weak description. This complements the Hermes Curator rather than replacing it.

## Planned usage

Requirements: Python 3.11 or later, and a Hermes Agent version with plugin hooks (`pre_llm_call`, `on_skill_lifecycle`) and `ctx.call_mcp`.

Start the server and index the skills:

```bash
git clone https://github.com/<owner>/greenknowledge
cd greenknowledge
pip install -e .
gk index ~/.hermes/skills
gk serve --host 127.0.0.1 --port 8765
```

Connect it to Hermes:

```bash
hermes mcp add gk --url http://127.0.0.1:8765/mcp
cp -r hermes-plugin/gk-bridge ~/.hermes/plugins/
hermes plugins enable gk-bridge
```

Plugins have no MCP access by default. Grant the bridge access to the `gk` server in `~/.hermes/config.yaml`:

```yaml
plugins:
  entries:
    gk-bridge:
      mcp_allowlist: ["gk"]
```

## Measuring

Do not trust a retrieval layer without measuring it. Write 20 to 30 real tasks with the skill the agent should have used in `bench/tasks.yaml`, then run:

```bash
gk bench bench/tasks.yaml
```

The harness reports Hit@1, Hit@3 and MRR, and compares indexing the full skill body against indexing metadata only.

## Security

- The server binds to `127.0.0.1` by default. If you expose it beyond localhost, set an access token.
- The fact store may hold sensitive information about your projects and infrastructure. Treat its database file accordingly.
- Content returned by the server is data for the agent, not instructions. Do not use it to drive approvals or command execution without validation.

## Roadmap

- [ ] Skill index and `prepare` returning skills only
- [ ] Hermes bridge plugin
- [ ] Benchmark harness
- [ ] Fact store: `remember`, `recall`, "Known" and "To verify" lines
- [ ] Feedback loop from `on_skill_lifecycle`
- [ ] Background tidying
- [ ] Optional reranker based on the SkillRouter models

## Related work

GreenKnowledge builds on ideas from these projects:

- [SkillRouter](https://github.com/zhengyanzhao1997/SkillRouter): retrieve-and-rerank skill routing, and the study behind the full-body indexing choice.
- [skill-retrieval-mcp](https://github.com/JayCheng113/skill-retrieval-mcp): semantic skill retrieval as an MCP server.
- [skill-router-mcp](https://github.com/skarborous/skill-router-mcp): a local server running the SkillRouter pipeline.
- skillmux: a local MCP server that routes tasks to skills in a SKILL.md vault.

These tools focus on finding skills. GreenKnowledge combines skill routing with a sourced fact store and an explicit list of things to verify, and injects the result automatically before each turn instead of relying on the agent to call a tool.

## Contributing

Issues and pull requests are welcome. For larger changes, please open an issue first to discuss the approach.

## License

See [LICENSE](LICENSE).
