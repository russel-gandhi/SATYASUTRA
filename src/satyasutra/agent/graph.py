"""The claim agent as a LangGraph: each step is a node, each tool use is reported, and the run can stop early.

    read_post -> extract -> (no claim? stop) -> registry + official + ground (in parallel) -> judge -> decide -> done

  extract   Gemini reads the post and lists its checkable claims (quotes must appear verbatim).
  registry  the Google Fact Check API: what fact-checkers have said about similar claims.
  official  Tier 1 official text (RBI, SEBI feeds): a different number for the same quantity is a contradiction (claims/contrast.py).
  ground    Gemini names the entities in each claim and Wikidata resolves them; context only, never evidence.
  judge     Gemini says whether each review covers the same claim, the opposite or a different one.
  decide    plain rules map rating and relation to a label; one ledger row per verdict.

Nothing here invents a label: Gemini only reads and compares, and the rules decide. A node that fails raises, so the trace reports
the failure instead of the run quietly finishing with a guess. The trace reads `tool_calls` from node updates (agent/trace.py).
"""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from ..claims import ExtractionResult, Grounded, Judgements, OfficialResult
from ..claims.rules import decide
from ..config import Config
from ..errors import SourceError
from ..ledger import LedgerStore
from ..pipeline import ClaimResult, VerifyOutcome, record_verdict
from ..schemas import Post
from ..sources import FactCheckResult

NODES = ("read_post", "extract", "registry", "official", "ground", "judge", "decide")


class ExtractionFailed(SourceError):
    """Claim extraction was blocked or failed, so there is nothing to check."""


class ModelBusy(SourceError):
    """The language model is out of quota or unreachable on every configured model: the run stops and says so, it does not guess."""


BUSY_REASONS = ("QuotaExhausted", "RateLimited", "Unreachable")


class AgentState(TypedDict, total=False):
    job: str
    text: str
    post: Post
    extraction: ExtractionResult
    found: dict[str, FactCheckResult]
    grounded: dict[str, Grounded]
    official: dict[str, OfficialResult]
    judged: dict[str, Judgements]
    tool_calls: Annotated[list, operator.add]       # registry and ground run in parallel and both report tools, so writes are merged


@dataclass
class AgentRun:
    """What the graph produced, for the caller to turn into a response. Filled in by the `decide` node."""
    outcome: VerifyOutcome | None = None
    grounded: dict[str, Grounded] | None = None


def build_claim_agent(
    cfg: Config,
    ledger: LedgerStore,
    make_post: Callable[[str], Post],
    extract: Callable[[Post], ExtractionResult],
    lookup: Callable[[str, str | None], FactCheckResult],
    judge: Callable[[str, list], Judgements] | None,
    ground: Callable[[str], Grounded] | None,
    run: AgentRun,
    official: Callable[[str], OfficialResult] | None = None,
) -> Any:
    def read_post(state: AgentState) -> dict:
        post = make_post(state["text"])
        return {"post": post, "tool_calls": [{"name": "read_post", "summary": f"{len(post.text)} characters"}]}

    def extract_node(state: AgentState) -> dict:
        ex = extract(state["post"])
        if ex.status == "failed" and ex.reason.startswith(BUSY_REASONS):
            raise ModelBusy("gemini_llm", ex.reason)
        if ex.status in ("blocked", "failed"):
            raise ExtractionFailed("gemini_llm", f"claim extraction {ex.status}: {ex.reason}")
        summary = f"{len(ex.claims)} checkable claim(s), {len(ex.non_claims)} not checkable ({ex.status})"
        return {"extraction": ex, "tool_calls": [{"name": "gemini: extract claims", "summary": summary}]}

    def after_extract(state: AgentState) -> list[str]:
        return ["registry", *(["official"] if official else []), "ground"] if state["extraction"].claims else [END]

    def registry(state: AgentState) -> dict:
        found, calls = {}, []
        for claim in state["extraction"].claims:
            found[claim.id] = lookup(claim.text_en, claim.lang)
            calls.append({"name": "fact-check registry", "summary": f"{len(found[claim.id].evidence)} review(s) of similar claims"})
        return {"found": found, "tool_calls": calls}

    def official_node(state: AgentState) -> dict:
        results, calls = {}, []
        for claim in state["extraction"].claims:
            r = official(claim.text_en)
            results[claim.id] = r
            suffix = f" (failed: {r.error})" if r.error else ""
            calls.append({"name": "official text (RBI, SEBI)", "summary": f"{len(r.passages)} passage(s) retrieved, {len(r.contrast)} contradiction(s){suffix}"})
        return {"official": results, "tool_calls": calls}

    def ground_node(state: AgentState) -> dict:
        grounded, calls = {}, []
        for claim in state["extraction"].claims:
            g = ground(claim.text_en) if ground else Grounded()
            grounded[claim.id] = g
            suffix = f" (failed: {g.error})" if g.error else ""
            calls.append({"name": "wikidata: ground entities", "summary": f"{len(g.entities)} entity(ies) found, {len(g.unmatched)} without an entry{suffix}"})
        return {"grounded": grounded, "tool_calls": calls}

    def judge_node(state: AgentState) -> dict:
        judged, calls = {}, []
        for claim in state["extraction"].claims:
            evidence = state["found"][claim.id].evidence
            j = judge(claim.text_en, evidence) if judge else None
            if j is not None:
                judged[claim.id] = j
                note = f"failed: {j.error}" if j.error else f"{len(j.relations)} review(s) compared with the claim"
                calls.append({"name": "gemini: same claim?", "summary": note})
        return {"judged": judged, "tool_calls": calls}

    def decide_node(state: AgentState) -> dict:
        ex = state["extraction"]
        out, calls = VerifyOutcome(state["post"], ex), []
        for claim in ex.claims:
            found = state["found"][claim.id]
            j = state.get("judged", {}).get(claim.id)
            off = state.get("official", {}).get(claim.id)
            outcome = decide(claim, [*found.evidence, *(off.contrast if off else [])], cfg, judged=j.relations if j else None)
            if off and off.error:
                outcome.reasons.append(f"official-text check unavailable ({off.error}): no official evidence used")
            if j and j.error:
                outcome.reasons.append(f"same-claim judge unavailable ({j.error}): strict word-overlap rule used")
            seq = record_verdict(cfg, ledger, claim, outcome, ex, j)
            out.results.append(ClaimResult(claim, outcome, seq, found.from_cache))
            calls.append({"name": "rules + ledger", "summary": f"{outcome.verdict.label}, ledger entry #{seq}"})
        run.outcome, run.grounded = out, state.get("grounded", {})
        return {"tool_calls": calls}

    g = StateGraph(AgentState)
    g.add_node("read_post", read_post)
    g.add_node("extract", extract_node)
    g.add_node("registry", registry)
    g.add_node("official", official_node)
    g.add_node("ground", ground_node)
    g.add_node("judge", judge_node)
    g.add_node("decide", decide_node)
    g.add_edge(START, "read_post")
    g.add_edge("read_post", "extract")
    g.add_conditional_edges("extract", after_extract, ["registry", "official", "ground", END])
    g.add_edge(["registry", *(["official"] if official else []), "ground"], "judge")
    g.add_edge("judge", "decide")
    g.add_edge("decide", END)
    return g.compile()
