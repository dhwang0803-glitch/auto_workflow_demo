# PLAN_13 — Self-Evaluating Agent (closed-loop policy_extract)

> **Status**: Draft (2026-05-07) · **Owner**: dhwang0803 · **Predecessor PLAN**: PLAN_12 (W3 closed, multimodal max pivot complete) · **Deadline**: 2026-05-18 (hackathon, **11 days**)

---

## 1. Motivation

The pipeline up to PLAN_12 is a **one-way DAG** — `policy_extract` throws one chunk at the LLM and forwards the result straight into the library. Two limits of that model:

1. **No recovery from extraction misses** — if the model drops some policies on a dense-table chunk (the systematic misses #8/#12/#15 in the Phase 0/1/2/3 sweep), no downstream stage knows. It stays buried until a human spots it in the review UI.
2. **No self-confidence introspection** — `needs_clarification` is the model self-reporting "I'm being vague," but it passes straight through with no extra validation. ADR-022 §8.2's "needs clarification → multi-turn follow-up" promise is unimplemented on the docs path.

Per the decision in memory `project_w3_then_langgraph_pivot.md`, we pivot to a **closed-loop self-evaluating agent**. Extract → self-evaluate → if insufficient, loop back (re-extract with a tuned prompt) → stop on termination condition. PLAN_12's one-shot model naturally absorbs into the 1-iter case of this graph.

**Why now**: Phase D established the `policy_extract` vision-recall baseline (10 cands / 5-chunk sample). On top of that baseline we have a measurable regression guard for **what** reflection has to recover — Phase D becomes the ground truth for PLAN_13.

## 2. Decision — adopt Langgraph + LangSmith

**Decision: langgraph (graph runtime) + LangSmith (observability).**

> **2026-05-07 decision-reversal note** — This PLAN's draft picked a DIY mini-state-machine. That comparison table priced the langgraph dependency cost but **missed the cost of building observability infra from scratch** (option A trace-in-response + option B structured log + option C DB persistence = we'd design/build/maintain all of it). When we include this cost honestly, the asymmetry flips the other way — langgraph is correct.

| Criterion | langgraph + LangSmith | DIY |
|---|---|---|
| Dependency cost | `langgraph` + `langsmith` ~50MB (noise next to 16.9GB GGUF) + one Modal image rebuild ~250s | 0 |
| Observability | LangSmith auto-captures node execution / state changes / conditional branches / timing. With `@traceable` the LLM payload joins the trace tree | We'd build it ourselves — response trace field + structured log + (future) DB persistence |
| Learning curve | StateGraph / node functions / conditional edges — a few hours | Standard Python async + Pydantic |
| Features we use | 3–5 nodes + conditional edges + state accumulation — langgraph offers it out of the box | Roll our own |
| Visualization / demo | LangSmith trace tree becomes the strongest visual asset to show judges. Public read-only run URL is shareable | Export mermaid manually + our Frontend would have to draw it |
| Video narrative | "Self-evaluating" is validated by an external tool (third-party trust) | Just our response dump |

**Adoption rationale**:
- **Observability infra zero-cost** — `LANGCHAIN_TRACING_V2=true` + API key is enough to work immediately. Building our own trace channel (options A+B+C) overwhelms the langgraph-dep cost.
- **Visual asset for the demo narrative** — showing judges "the AI evaluated its own result and re-extracted" via the LangSmith trace tree is faster and more credible than drawing a trace UI in our own Frontend.
- **Node signatures are simple async functions** — `LLMBackend` is cleanly decoupled from langchain LLM wrappers, so we call it directly inside nodes. No Protocol change.
- **Replay / comparison** — LangSmith persists per-run input/output. Reuses straight as a tool for recall-regression measurement / per-iteration comparison / retro inspection during prompt tuning.

**Privacy caveat**: LangSmith is a third-party SaaS — prompt/response leaves the boundary. The hackathon fixture (gitlab handbook, MIT, public) is fine, but real-customer data would require a decision on self-hosted LangSmith or trace sampling. Within this PLAN scope it's zero-issue. Note it in the README / submission materials.

**Free-tier limit**: LangSmith free 5K traces/mo. The Modal smoke here (5-chunk × 2 modes × 2 iter ≈ 20 traces/run) + dev-time manual runs stay well inside the cap. If we approach it: trace sampling or paid tier.

## 3. Scope

### In Scope (this PLAN, ~5–7 days)

- **Define a langgraph StateGraph** — 3 nodes `extract` / `self_eval` / `reflect` + conditional edges. State is a Pydantic model (langgraph 0.2+ supports it).
- **Node 1: extract** — Thin async wrapper around the existing `policy_extract.extract_policies`. Iteration 1 is vanilla; iteration 2+ has the reflect-injected hint added to the system prompt.
- **Node 2: self_eval** — Takes the extraction result + the chunk (text/image) and outputs an `EvalReport`. Deterministic rules first + LLM judge fallback (added in PR-D).
- **Node 3: reflect** — Converts `EvalReport.coverage_concerns` into a prompt hint (pure string composition, no LLM call).
- **Conditional edges** — After `self_eval`, if `decision == "converge"` → END / if `decision == "retry"` and `iter < max_iter` → reflect / otherwise → END.
- **Termination**: `max_iter=2` (one loopback at most). Langgraph's built-in step limit gives a second safety net against infinite loops.
- **HTTP exposure** — `POST /v1/policy/extract_reflective` (coexists with the existing `/v1/policy/extract` — both A/B comparison and regression-guard can run both). Response carries `agent_trace` (state.iterations dump) + LangSmith run URL.
- **LangSmith integration** — `LANGCHAIN_TRACING_V2=true` + Modal Secret `langsmith-api-key` + `LANGCHAIN_PROJECT=auto-workflow-policy-extract`. Wrap backend `complete()` in `@traceable` so the LLM payload also lands in the trace tree.
- **Regression guard** — Extend `scripts/phase_d_vision_smoke.py` or a sibling script to measure both reflective and single-shot. Print the LangSmith run URL to stdout too, so retro comparisons are an asset.
- **Unit tests** — Validate per-node determinism + termination + infinite-loop prevention with a stub backend. Disable LangSmith in tests via `LANGCHAIN_TRACING_V2=false`.

### Out of Scope (post-W4 / future)

- Reflective-ization of other call sites (`compose`, `gap_analyze`, `answers_to_skill`) — Phase D already decided to skip Phase F. Their inputs are a fixed single expression, so self-eval has weak meaning.
- Cross-domain generalization of the LLM-judge mode — this PLAN is scoped to the policy_extract domain.
- Automatic prompt evolution / RL — static reflection prompts only.
- Automatic conflict detection, observation-based skill candidates — kept as ADR-022 follow-ups.
- **DB persistence (`agent_runs` table)** — Database-branch work + workspace-scope decision needed, too heavy for 11 days. LangSmith covers run-level persistence (inside the free tier). Real production would self-host LangSmith or persist in our own DB, separate PR.
- **Our own Frontend trace UI** — This PLAN's response carries the `agent_trace` JSON, but having the wizard / skills library view visualize it is W4 demo work. For judges, show the LangSmith UI directly first.
- **Trace sampling / PII masking** — Out of scope here because the hackathon fixture is public. For real customers, use the langsmith client's `redact` hook or switch to self-hosted, in a separate PR.

## 4. Architecture

### 4.1 Graph

```
                ┌────────────┐
   start  ──►   │  extract   │
                └─────┬──────┘
                      │ drafts
                      ▼
                ┌────────────┐
                │ self_eval  │
                └─────┬──────┘
                      │ EvalReport
            ┌─────────┴──────────┐
            │                    │
   converge / max_iter        retry
            │                    │
            ▼                    ▼
        ┌───────┐         ┌───────────┐
        │ end   │         │  reflect  │── (tune prompt) ──► extract (iter+1)
        └───────┘         └───────────┘
```

Termination branches:
- self_eval `decision == "converge"` → end
- iteration > max_iter → end (reason="max_iter_exhausted")
- reflect produces no prompt change (no-improvement) → end (reason="no_change")

### 4.2 State (Pydantic, langgraph-compatible)

```python
from typing import Annotated
from operator import add
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END

class AgentIteration(BaseModel):
    drafts: list[SkillDraft]
    eval: "EvalReport"
    prompt_hint: str = ""  # hint reflect injected for the next iter (iter 1 is "")

class AgentState(BaseModel):
    chunk: str
    images: list[str] | None = None
    domain: DomainCategory = "other"
    max_iter: int = 2
    # langgraph reducer: nodes returning [new_iter] auto-append
    iterations: Annotated[list[AgentIteration], add] = Field(default_factory=list)
    terminated: bool = False
    reason: str = ""  # "converge" | "max_iter_exhausted" | "no_change" | "schema_error"
```

`Annotated[..., add]` is langgraph's reducer hint — nodes only need to return `{"iterations": [new_iter]}` and the framework auto-appends. Node signatures stay simple: `async def extract(state: AgentState) -> dict`.

### 4.3 EvalReport (self_eval output)

`self_eval` is a hybrid of deterministic rules + LLM judge:

```python
class EvalReport(BaseModel):
    decision: Literal["converge", "retry"]
    coverage_concerns: list[str] = []  # natural language, used by reflect as a prompt hint
    schema_issues: list[str] = []      # nearly always 0 — _parse_response already filters
    rationale: str = ""                # one-line judge summary (debug + demo)
```

Rules (deterministic first to save LLM calls):
1. If drafts is empty and the chunk contains policy keywords (e.g., "must", "shall", "approve", "require") → retry
2. If every draft has needs_clarification=True → retry
3. If iteration > 1 and the new drafts are identical to the previous (no improvement) → converge (marked no_change)
4. If none of the rules match, call the LLM judge once — show chunk + drafts and ask "anything missed?" to get a natural-language hint

The LLM judge's max_tokens is 256 (short critique) — extra latency ~5–10s.

### 4.4 Reflect

`reflect` is **pure string composition with no LLM call**:
- Append `coverage_concerns` to the end of `_system_prompt` as "Previous pass may have missed: <list>. Re-examine the chunk." and pass it through as the system prompt for the next iter.
- If the same hint appears twice in a row → no-improvement → converge.

### 4.5 Latency budget

| Scenario | Call count | Tokens | Time (warm) |
|---|---|---|---|
| 1-iter (most common) | extract × 1 | 4096 | ~30s |
| 1-iter + judge | extract × 1 + judge × 1 | 4096 + 256 | ~35–40s |
| 2-iter (full retry) | extract × 2 + judge × 1 | 4096×2 + 256 | ~65–70s |

On Phase D's 5-chunk sample, averaging 6.1s × 2 + judge ≈ 18–20s/chunk reflective. The full 23-chunk sweep takes ~5–8 min — one Modal cold start + warm processing.

#### PR-D measurement (2026-05-07, text-mode, max_iter=2, 5-chunk sample)

| Mode | Avg latency | Total candidates |
|---|---|---|
| single-shot (`/v1/policy/extract`) | 1.5s | 1 |
| reflective (`/v1/policy/extract_reflective`) | 4.0s | **4** |
| Ratio | **2.67x latency** | **4.00x recall (+3 cand)** |

Text-mode single-shot 1 cand matches the Phase D result (text-only baseline). Reflective recovers to 4 cands — `max_iter=2`'s one extra iteration + judge recovered +1 cand each at chunks 6/11/16. Chunks 0/22 have no policy (converge 1-iter). Chunk 11 hit max_iter_exhausted (judge decided retry + budget ran out) — bumping to `max_iter=3` may recover further. Trace-tree verification after the user syncs the LangSmith API key is the immediate next step (the smoke itself measures fine without a trace).

### 4.6 Regression guard

`scripts/phase_d_vision_smoke.py` is the baseline. New sibling `scripts/plan_13_reflective_smoke.py`:
- same deterministic sample (5 chunks)
- runs both modes (single-shot + reflective)
- prints recall delta + latency delta
- expected: recall ≥ Phase D baseline (1 text / 10 vision), latency ≤ 2x

Goal — minimum bar is "reflective doesn't regress baseline." Any improvement (even +1 cand) is OK (for the hackathon demo narrative).

**PR-D measurement (2026-05-07)**: text-mode alone, single-shot 1 cand → reflective **4 cands (+3, 4× recovery)**. Latency 2.67× (slightly over the ≤ 2× goal). No recall regression (monotonically up). Vision-mode measurement happens in a separate step after the user syncs the LangSmith key — this measurement alone is enough for the demo narrative ("the AI reviewed its own result and recovered +3 policies").

## 5. Directory + file layout

```
AI_Agent/app/agents/                          ← (new)
├── __init__.py
├── policy_extract_agent.py                    ← StateGraph builder + node functions + compile()
├── state.py                                   ← AgentState (Pydantic + Annotated reducers)
├── eval.py                                    ← EvalReport + self_eval rules + judge prompt
└── tracing.py                                 ← LangSmith setup helper (no-op without env)

AI_Agent/app/main.py                           ← (modified) add /v1/policy/extract_reflective route
AI_Agent/app/models/skills.py                  ← (modified) PolicyExtractReflectiveRequest/Response (agent_trace + langsmith_run_id)

AI_Agent/scripts/modal_app.py                  ← (modified) add langsmith-api-key to Modal Secret

AI_Agent/tests/test_policy_extract_agent.py    ← (new) stub-backend graph validation (tracing OFF)
AI_Agent/tests/test_policy_extract_agent_route.py ← (new) route integration

AI_Agent/scripts/plan_13_reflective_smoke.py   ← (new) Modal live regression measurement + LangSmith run-URL output
AI_Agent/pyproject.toml                        ← (modified) add langgraph + langsmith dependency
```

**Why a new `app/agents/`** — `services/` is the single-LLM-call unit. An agent composes several services + state, so the meaning differs. Future reflective wrappers go in the same directory.

**`tracing.py`** — If `LANGCHAIN_TRACING_V2` is missing or false, swap `@traceable` for a no-op decorator. Unit tests pass without a LangSmith key locally / in CI.

### 5.1 Dependency addition (pyproject.toml)

```toml
dependencies = [
    # ... existing ...
    "langgraph>=0.2",   # StateGraph + conditional edges + reducer
    "langsmith>=0.1",   # @traceable + run-URL helper. With tracing disabled it's import-only, runtime cost 0
]
```

### 5.2 Environment variables (Modal Secret)

| Key | Purpose | If absent |
|---|---|---|
| `LANGCHAIN_TRACING_V2=true` | Master switch turning tracing ON | Traces not sent (local/CI) |
| `LANGCHAIN_API_KEY` | LangSmith auth (Modal Secret `langsmith-api-key`) | Tracing silently disabled |
| `LANGCHAIN_PROJECT` | LangSmith project name (e.g., `auto-workflow-policy-extract`) | Falls back to the default project |

The existing `scripts/sync-modal-secrets.py` (PR #157 merged) syncs `langsmith-api-key-staging` from GCP Secret Manager into a Modal Secret. Creating the GCP secret is an external predecessor of this PLAN's PR-D.

## 6. PR split

Small-unit merges (`feedback_test_before_pr.md`).

| # | Content | Depends on |
|---|---|---|
| **(this PR)** | PLAN_13 doc-only | — |
| **PR-A** | pyproject deps (`langgraph`, `langsmith`) + `app/agents/state.py` + `app/agents/eval.py` (deterministic rules only, no LLM judge yet) + `app/agents/tracing.py` (no-op fallback) + unit tests | this PR |
| **PR-B** | `policy_extract_agent.py` StateGraph builder + extract/reflect nodes + conditional edges + termination + unit tests (stub backend, tracing OFF) | PR-A |
| **PR-C** | `/v1/policy/extract_reflective` route + request/response schemas (`agent_trace` + `langsmith_run_id`) + route integration tests | PR-B |
| **PR-D** | LLM judge node + LangSmith integration (`@traceable` LLM wrapper + Modal Secret sync) + Modal smoke (`plan_13_reflective_smoke.py`) + recall / latency measurement + LangSmith run-URL verification + update this doc's measurements | PR-C |
| **(W4)** | Demo integration — screen-capture LangSmith trace tree for the video / link trace URL on Frontend cards (optional) | PR-D |

**PR merge procedure**: each PR is not merged until the user explicitly approves (`feedback_no_auto_merge.md`).

## 7. Risks + mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| LLM judge hallucinates `coverage_concerns` | reflect adds noise from wrong hints | Force the judge prompt: "list ONLY the items the chunk explicitly states" + the no-improvement converge rule when the same hint repeats |
| Modal cold start + accumulated multi-calls | Reflective sweep unrealistically slow | Generous `scaledown_window` + smoke is deterministic 5-sample only, full 23-sweep behind an option flag |
| Infinite loop (max_iter ignored bug) | Cost blow-up + tests hang | langgraph's `recursion_limit` + our `iter < max_iter` conditional edge as double safety. Unit test asserts max+1 raises |
| Reflective recalls lower than single-shot | Phase D baseline regression — demo regression | `plan_13_reflective_smoke.py` runs both modes for regression compare. On regression, tune reflect prompt or back this PLAN out (keep single-shot + partial adoption) |
| LLM-judge extra call doubles cost / latency | Modal cost + awkward demo | Keep judge max_tokens=256 short. Skip judge when deterministic rules decide retry. Smaller-model option is future |
| **LangSmith external data egress** | Real-customer prompt/response goes to a third-party SaaS — compliance / PII concern | Hackathon fixture is public material (gitlab handbook MIT) — irrelevant. For real customers: self-hosted LangSmith / `langsmith` client `redact` hook / trace sampling. Note in README / submission. |
| **Exceed LangSmith free-tier 5K traces/mo** | Trace loss + traces missing at demo time | Smoke run is 5-chunk × 2 modes × 2 iter ≈ 20 traces/run — plenty of margin. Paid tier is future. Monitor the LangSmith dashboard near the limit |
| **LangSmith service down / network failure** | Traces not delivered — feature itself still works, but the trace tree can't be shown at demo | `langsmith` client silently logs ingestion failures and the node keeps running (library default). Demo fallback: show the response `agent_trace` JSON dump directly |
| **langgraph dependency conflict** | Modal image build / version conflict with other packages | Only set the lower bound `langgraph>=0.2,<1.0`. On conflict, pin a specific version. Verify Modal rebuild before merging PR-A (`feedback_test_before_pr.md`) |
| This PLAN doesn't finish by the W4 video | Unfinished demo | Explicit scope-cut authority — PR-A/B/C alone is enough for a LangSmith-trace demo. PR-D's LLM judge if time permits |

## 8. Unresolved decisions (settle during implementation)

1. **judge model = same as main model?** — start with the same Gemma 4 26B-A4B. Compare smaller models in future.
2. **Loop prompt-hint format** — start with simple string concat. If effect is weak, promote to a separate system-prompt section (`## Previous pass`).
3. **User surfacing (UI)** — this PLAN goes up to the backend response + LangSmith URL. Linking the trace URL on wizard/skills cards in Frontend is W4.
4. **Drafts output after converge = final iter only? union? best-by-rule?** — start with "final iter only" (assume reflect augmented previous-iter results, so it's a superset). After measurements, decide on union.
5. **Modal warm-keeping strategy** — does the sweep need a keepalive ping between cold starts? Decide based on PR-D measurement.
6. **Public visibility of LangSmith run URL** — to put it in the video we need per-run share links (LangSmith's public read-only). Check whether auto-generate is possible + how, during PR-D work.

## 9. Milestones (5/7 → 5/18)

| Date | Work | Status |
|---|---|---|
| 5/7 (today) | Merge doc PR + PR-A + PR-B + PR-C + PR-D all | **Done (same-day compressed)** |
| 5/8-13 | Wizard-flow integration + live demo scenarios (PLAN_13 closes, W4 enters) | — |
| 5/14-15 | Burn-in + submission assets (README, demo video) | — |
| 5/16-17 | LangSmith trace-tree video capture + vision-mode measurement after user-environment secret registration | — |
| 5/18 | Submit | — |

+5d buffer. PR-D finished in the same session → time secured for W4 video / integration. Text-mode alone proves +3 cand recall recovery — demo narrative secured.

## 10. Related ADRs / memory / docs

- **ADR-022** (`docs/context/decisions.md`) — parent ADR. This PLAN is a predecessor of ADR-022 §8.2 (ambiguous policy multi-turn follow-up) + "observation-based skill candidates / adversarial harness automation" in the downstream-impact list
- **PLAN_12** (`AI_Agent/plans/PLAN_12_skill_bootstrap.md`) — direct parent of this PLAN (W3 closed, multimodal pivot absorbed)
- memory `project_w3_then_langgraph_pivot.md` — source of the 2026-05-06 decision
- memory `project_multimodal_max_pivot.md` — Phase D baseline measurement (regression-guard ground truth)
- memory `reference_phase_d_vision_smoke.md` — regression-measurement reproduction command
- memory `project_session_20260506_recall_recovery.md` — aggressive prompt sweep result (the miss patterns #8/#12/#15 reflective must recover)
- memory `feedback_test_before_pr.md` — duty to externally validate before splitting PRs
- memory `feedback_no_auto_merge.md` — every PR in this PLAN waits for explicit approval before merge

---

## 11. Agent loop refactor (ADR-024, 2026-05-09)

### 11.1 Motivation

§1–§10 describes a **deterministic langgraph workflow** — the `extract → self_eval → reflect` node order is decided by hand-coded conditional edges (`decide_after_eval`, `decide_after_reflect`). In Anthropic "Building effective agents" (2024-12) terminology, this is a **workflow, not an agent** — the LLM doesn't decide the next action.

The hackathon narrative ("**the system learns**") demands the "agent" label on two axes:

1. **Storytelling 30%** — "agent" is a strong word to the judging audience. Calling it a workflow tones the narrative down by half.
2. **Impact & Vision 40%** — PLAN_14 (HITL → personal_skill recovery)'s autonomous-learning narrative is "the system watches user behavior and tunes its own output," which only stays consistent if there's a model where the LLM calls tools (retrieval) on its own decisions. Hardcoding persona-skill retrieval on top of a workflow sounds like "the same automation with one more condition."

### 11.2 Decision

**Expose `extract_policies` / `evaluate_coverage` / `finalize` as tools, and let a ReAct-style agent loop drive flow via the LLM's `<tool_call>` / `<finish>` decisions**. Split into 4 PRs (α/β/γ/δ/ε/ζ):

| PR | Content | Validation |
|---|---|---|
| **α** (this PR) | Tool dataclass + ReAct loop + `<tool_call>`/`<finish>` parser. Don't touch existing langgraph (parallel) | 22 new units / 223 regression 0 |
| **β** | Define existing extract / judge / finalize as Tools. Remove langgraph from `policy_extract_agent.py`, swap in the agent loop | Live smoke reproduces +3 cand recall |
| **γ** | `personal_skills` table + BGE-M3 indexing + `search_personal_skills(user_id, query)` tool | DB migration + tool unit |
| **δ** | `search_industry_baselines(domain, query)` tool — BGE-M3 indexing over the seed YAML policies | tool unit + agent integration |
| **ε** | Deterministic tools `validate_skill_schema(draft)` + `cite_source_url(draft, domain)` | tool unit |
| **ζ** | Recapture D3 evidence (PR #168 supersede). New NDJSON + new screenshots + new README narrative | Live smoke green |

### 11.3 Tool catalog (final after β–ε)

```
extract_policies(chunk, hint?, domain, images?)
    → list[SkillDraft]
    LLM extract call. Empty hint = iter 1, populated = iter 2+ retry.

evaluate_coverage(drafts, chunk)
    → {decision: "converge"|"retry", coverage_concerns: list[str], rationale: str}
    PLAN_13 §4.3 deterministic rules (eval.py) + optional LLM judge (judge.py).
    Only difference is the agent calls it directly; internals unchanged.

search_personal_skills(user_id, query, k=3)
    → list[SkillDraft]
    Top-k BGE-M3 cosine over this user's previously approved skills.
    Queries the personal_skills table PLAN_14 fills. Empty result possible (cold start).

search_industry_baselines(domain, query, k=3)
    → list[{policy_id, name, sources}]
    Retrieval over BGE-M3 indexing of seed YAML policies. Intended as
    a grounding hint injected into the prompt for domain-standard policies.

validate_skill_schema(draft)
    → {valid: bool, issues: list[str]}
    Deterministic: condition+action non-empty / length limits / format etc.

cite_source_url(draft, domain)
    → {sources: list[{title, url}], source_kind: ...}
    YAML seed match → ADR-022 §8.4 source_kind classification + URL recovery.

finalize(drafts)
    → Termination signal. Equivalent to <finish> but separated to make
    "drafts is the explicit output" obvious.
```

### 11.4 ReAct wire format (ADR-024 §3)

Every assistant turn ends in exactly one of:

```
<tool_call name="TOOL_NAME">
{...JSON args...}
</tool_call>
```
or
```
<finish>
{...JSON final result...}
</finish>
```

Observation in the next user turn:
```
<tool_result tool="TOOL_NAME">
{JSON serialized return value}
</tool_result>
```

No Gemma 4 native tool calling — same posture as `judge.py`'s prompt-engineered JSON output pattern. Zero added model dependency.

### 11.5 Agent loop termination reasons

| reason | Meaning |
|---|---|
| `finish` | Model emitted `<finish>` |
| `parse_error` | Model output parses to neither `<tool_call>` nor `<finish>` |
| `tool_not_found` | Called an unregistered tool (one shot at recovery via obs error, repeated → no_progress) |
| `max_iter_exhausted` | Budget exceeded. Default 8 (search × 2 + extract × 2 + eval × 2 + finish + slack) |
| `no_progress` | Same (tool, args) twice in a row. Model is stuck — cut before max_iter |

### 11.6 Regression guard (PR-β validation premise)

After PR-α we only added the agent-loop infra; langgraph stays. **At the point PR-β removes langgraph and swaps in the agent loop**, reproducing the D3 live smoke result is the regression guard:

- GitLab handbook 5-chunk sample, max_iter=2 (β) or 8 (after γ–ε add retrieval)
- single-shot 1 cand vs reflective 4 cand → **maintain the +3 recall delta**
- On regression: tune the prompt (reinforce the tool-usage guide in system_goal) or shrink scope

`tests/fixtures/gitlab_handbook_excerpt.pdf`'s 23-chunk decomposition through `services.document_parser` is deterministic → recall measurements are not trapped by model nondeterminism.

### 11.7 Connection to PLAN_14

PR-γ (`search_personal_skills` tool + `personal_skills` table) is half the closed-loop infrastructure for PLAN_14:

- PLAN_14 recovers personal_skill candidates from user edit diffs → stored in `personal_skills`
- On the next extraction call, the agent decides on its own to call `search_personal_skills` → this user's past patterns naturally enter the extraction context
- The user notices "huh, the things I usually add are already in there" → satisfies ADR-023 §6 narrative invisibility

If PLAN_14 only fills the `personal_skills` table, this PLAN's retrieval tool automatically uses that data. **PLAN_14 PR count compresses from 9 → 7–8** (search infra becomes sunk cost).
