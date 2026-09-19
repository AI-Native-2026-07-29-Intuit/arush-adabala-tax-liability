# taxcalc-agent-svc/PROMPT_JOURNAL.md

What Claude was asked, what it produced, and what happened to that output before it reached the
tree. The point of keeping this is the third column: the transcripts below are useful mainly
where the generated answer was *plausible and wrong*, because those are the places a reviewer
would otherwise have no reason to look twice.

The pattern across all three is the same as W7 D4's. Claude's output was idiomatic for how
LangGraph is usually written about, and every correction came from **running** it rather than
reading it. Two of the three defects below produce no exception at all — they are silent, and a
review that only read the diff would have passed them.

## W7 D5 transcripts

### 1. Three-node StateGraph with a supervisor and parallel fan-out

**Prompt to Claude:**

> Extend my W7 D4 single-node LangGraph (HITL around orders.create_refund)
> into a W7 D5 three-node graph: AgentState TypedDict (question, tenant_id,
> docs, tool_results, answer) with reducers on the parallel slots, a
> supervisor router returning list[Send], retrieval + api + synthesis
> nodes, PostgresSaver checkpointer, recursion_limit=25 on compile. Use
> claude-sonnet-4-5 and tenant ids tenant-a/b/c only.

**Raw Claude response (excerpt):**

```python
class AgentState(TypedDict):
    question: str
    tenant_id: str
    docs: list[dict]
    tool_results: dict
    answer: str | None

def supervisor(state: AgentState) -> list[Send]:
    q = state["question"].lower()
    targets = []
    if any(t in q for t in ("policy", "docs")):
        targets.append(Send("retrieval_agent", state))
    if any(t in q for t in ("order", "refund")):
        targets.append(Send("api_agent", state))
    return targets

cp = PostgresSaver.from_conn_string(settings.postgres_url)
cp.setup()
graph = sg.compile(checkpointer=cp)
```

**Used as is / Modified / Rejected:** **Modified**, in four places, three of which are silent
failures.

- **The parallel slots have no reducers.** `docs: list[dict]` and `tool_results: dict` are bare,
  so they use LangGraph's default channel, whose update rule is last-write-wins. The supervisor
  fans out to both workers in the same super-step; whichever finishes second silently erases the
  other's contribution and synthesis answers from half the evidence. No exception, no log line.
  Shipped with `Annotated[list[dict], operator.add]` on `docs` and a key-wise merger on
  `tool_results` — `operator.add` cannot be used there, because `dict + dict` raises `TypeError`
  at fan-in, at runtime.

- **`PostgresSaver` does not work with async nodes.** Every node body here is `async`, so every
  call site is `ainvoke`/`astream_events`, so LangGraph drives the checkpointer's *async*
  interface — which the synchronous saver inherits from `BaseCheckpointSaver` as
  `raise NotImplementedError`. Measured: it fails inside `AsyncPregelLoop.__aenter__` at
  `await self.checkpointer.aget_tuple(...)`, before any node runs. Shipped with
  `AsyncPostgresSaver` and `await cp.setup()`.

- **An empty routing plan answers blind.** Claude's supervisor returns `[]` for a question
  matching no keyword, which routes straight to synthesis with no docs and no tool results. That
  is the worst available outcome: a confident, well-formed, entirely ungrounded answer. Shipped
  defaulting to `retrieval_agent` — a router that does not understand the question should ground
  it, not guess.

- **`recursion_limit` on compile only.** Pinned on every call site too, since the limit's whole
  purpose is that it does not move when a library default does.

### 2. Instructor-typed FinalAnswer with a refusal path

**Prompt to Claude:**

> Write the synthesis node: instructor.from_anthropic(AsyncAnthropic()),
> response_model=FinalAnswer(text, citations, confidence), max_retries=2.
> The system prompt must force a refusal path when both docs and
> tool_results are empty - low confidence, no fabricated citations.

**Raw Claude response (excerpt):**

```python
class Citation(BaseModel):
    doc_id: str
    quote: str

class FinalAnswer(BaseModel):
    text: str
    citations: list[Citation] = []
    confidence: float

_SYSTEM = "You are a helpful assistant. Answer based on the provided context. "
          "If you don't have enough information, say so."
```

**Used as is / Modified / Rejected:** **Modified.** The structure is right and three details are
not.

- **No `ConfigDict(extra="forbid")`.** Without it, a model that invents `"sources"` alongside
  `"citations"` validates cleanly, the invented field is dropped, and the answer ships with an
  empty citation list and no error anywhere. Forbidding extras turns that into a validation
  error `max_retries=2` can actually repair — which is what makes the retry a *repair* rather
  than a re-roll, since Instructor feeds the validation error back to the model.

- **`citations: list[Citation] = []` is a shared mutable default.** Every instance ever
  constructed shares one list. Shipped as `Field(default_factory=list)`.

- **"Say so" is not a refusal path.** It produces a polite paragraph that is indistinguishable,
  to every downstream consumer, from an answer. The refusal has to be *machine-checkable*, which
  is what `confidence` is for: the shipped prompt names the threshold explicitly ("set confidence
  below 0.4 and return a refusal in the text field. Never fabricate citations"), and the eval,
  the sampler and the client all branch on the number rather than trying to detect an apology in
  prose.

Also shipped: `quote` bounded at `min_length=10, max_length=240`. A floor rejects a token-long
"quote" that supports nothing; a ceiling rejects a model that pastes a whole chunk back instead
of choosing the relevant sentence.

### 3. Trajectory eval with a RAGAS faithfulness gate

**Prompt to Claude:**

> Write evals/trajectory.py: a Scenario dataclass and 20 rows, each with
> qid, question, tenant_id, expected_nodes and expected_answer_substring.
> trajectory_match returns 1.0 when the actual nodes contain the expected
> sequence. run_eval aggregates trajectory match alongside RAGAS
> faithfulness, and the CI gate fails on a trajectory-match-floor breach or
> a cost-per-run regression over fifteen percent.

**Raw Claude response (excerpt):**

```python
def run_eval(graph, scenarios=SCENARIOS):
    for sc in scenarios:
        cfg = {"configurable": {"thread_id": f"eval-{sc.qid}"},
               "recursion_limit": 25}
        run_state = graph.invoke({...}, config=cfg)
        nodes = tuple(run_state.get("__visited_nodes", ()))
    ...
    ragas_scores = evaluate(rows, metrics=[faithfulness])
    summary = {"faithfulness": float(ragas_scores["faithfulness"]), ...}
```

**Used as is / Modified / Rejected:** **Used as is for the scenario shape; modified in four
places**, and the third of these is the most interesting thing in this journal because the gate
caught it by failing.

- **`__visited_nodes` does not exist.** LangGraph's channels carry the declared state slots plus
  its own bookkeeping; none of them is an ordered record of which node bodies ran. The closest
  thing, `metadata.writes`, is per-super-step, is a checkpointer implementation detail, and is
  empty for a graph compiled without one — so an eval written against it would pass only in the
  configuration that has a database. Shipped as an explicit `visited_nodes` state slot with
  `operator.add`: one line per node, and an assertion that holds for every caller.

- **`graph.invoke` with async nodes** raises `TypeError: No synchronous function provided`.
  Shipped as `await graph.ainvoke`.

- **`thread_id=f"eval-{sc.qid}"` makes the cost gate fire forever.** This one was found by the
  gate itself. The id is stable across runs, the checkpointer persists state under it, and
  `cost_usd_e5` carries `operator.add` — so the second run of the suite *resumes the first run's
  checkpoint* and reports double the cost. Observed directly: two consecutive runs reported 508
  then 1016 (1e-5 USD), a +100% regression in which nothing had changed, and the gate correctly
  failed a build for it. Shipped with a per-run namespace, `f"eval-{run_ns}-{sc.qid}"`, after
  which three consecutive runs report 508, 508, 508. The checkpointer's resume behaviour is
  proven in `tests/test_checkpointer_resume.py`, which is where that claim belongs — an eval that
  silently depends on it is an eval that measures the wrong thing.

- **`float(ragas_scores["faithfulness"])` on a failed evaluator.** Claude's version has no path
  for RAGAS not running. Without a credential it raises, and the obvious fix — defaulting to a
  passing number — is worse: it reports a green build for a measurement that never happened.
  Shipped returning `None`, surfaced as `faithfulness_measured: false`, and **failing** the gate
  unless `--allow-unmeasured-faithfulness` is passed, which prints a banner saying the metric was
  declared rather than measured. This is the W7 D3 lesson applied directly; that repository has
  already been bitten once by a skip rendering as a green tick.

---

## What this says about the tool

Across three transcripts, Claude produced correct-looking LangGraph in every case, and the three
defects that mattered most were all **invisible without running the code**: a missing reducer
that loses data with no error, a sync checkpointer that fails only under async execution, and a
stable thread id that makes an eval measure its own history. The one defect a careful reader
might have caught unaided — the shared mutable default — was the least consequential.

The practical conclusion is not "check Claude's output more carefully". It is that the checks
have to be **executed**: a reducer assertion read off `__annotations__`, a checkpointer test that
closes its pool before reopening one, and a cost gate that runs twice. Each of those exists in
this project because reading the code was not enough.
