"""The claim agent as a LangGraph: each step is a node, each tool use is reported, and the run can stop early.

    read_post -> extract -> (no claim? stop) -> registry + official + ground (in parallel) -> judge -> decide -> done

  extract   Gemini reads the post and lists its checkable claims (quotes must appear verbatim).
  registry  the Google Fact Check API: what fact-checkers have said about similar claims.
  web       a live search of news outlets across the web; reputable outlets and fact-checkers can decide, others are shown (claims/web.py).
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

from ..claims import ExtractionResult, Grounded, Judgements, OfficialResult, WebResult
from ..claims.rules import decide
from ..config import Config
from ..errors import SourceError
from ..ledger import LedgerStore
from ..pipeline import ClaimResult, VerifyOutcome, record_verdict
from ..schemas import Post
from ..sources import FactCheckResult

NODES = ("read_post", "extract", "registry", "official", "web", "ground", "judge", "decide")


class ExtractionFailed(SourceError):
    """Claim extraction was blocked or failed, so there is nothing to check."""


class ModelBusy(SourceError):
    """The language model is out of quota or unreachable on every configured model: the run stops and says so, it does not guess."""


class ModelDailyLimit(ModelBusy):
    """The key's daily allowance is used up. Waiting a minute does not help, so this is its own class and the pages say something different."""


BUSY_REASONS = ("QuotaExhausted", "RateLimited", "Unreachable")
DAILY_REASON = "QuotaExhausted"          # the client raises this only when the provider says the per-day allowance is used up


class AgentState(TypedDict, total=False):
    job: str
    text: str
    post: Post
    extraction: ExtractionResult
    found: dict[str, FactCheckResult]
    grounded: dict[str, Grounded]
    official: dict[str, OfficialResult]
    web: dict[str, WebResult]
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
    web: Callable[[str], WebResult] | None = None,
) -> Any:
    def read_post(state: AgentState) -> dict:
        post = make_post(state["text"])
        return {"post": post, "tool_calls": [{"name": "read_post", "summary": f"{len(post.text)} characters"}]}

    def extract_node(state: AgentState) -> dict:
        ex = extract(state["post"])
        if ex.status == "failed" and ex.reason.startswith(BUSY_REASONS):
            raise (ModelDailyLimit if ex.reason.startswith(DAILY_REASON) else ModelBusy)("gemini_llm", ex.reason)
        if ex.status in ("blocked", "failed"):
            raise ExtractionFailed("gemini_llm", f"claim extraction {ex.status}: {ex.reason}")
        summary = f"{len(ex.claims)} checkable claim(s), {len(ex.non_claims)} not checkable ({ex.status})"
        return {"extraction": ex, "tool_calls": [{"name": "gemini: extract claims", "summary": summary}]}

    def after_extract(state: AgentState) -> list[str]:
        return ["registry", *(["official"] if official else []), *(["web"] if web else []), "ground"] if state["extraction"].claims else [END]

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

    def web_node(state: AgentState) -> dict:
        results, calls = {}, []
        for claim in state["extraction"].claims:
            r = web(claim.text_en)
            results[claim.id] = r
            suffix = f" (failed: {r.error})" if r.error else ""
            calls.append({"name": "web news search", "summary": f"{r.hits} headline(s) found, {len(r.supporting)} outlet(s) report it as fact, {len(r.debunking)} debunk it{suffix}"})
        return {"web": results, "tool_calls": calls}

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
            net = state.get("web", {}).get(claim.id)
            outcome = decide(claim, [*found.evidence, *(off.contrast if off else []), *(net.evidence if net else [])], cfg, judged=j.relations if j else None)
            if off and off.error:
                outcome.reasons.append(f"official-text check unavailable ({off.error}): no official evidence used")
            if net and net.error:
                outcome.reasons.append(f"web news search unavailable ({net.error}): no web evidence used")
            if j and j.fallback:
                outcome.reasons.append(f"same-claim judge: Gemini unavailable ({j.fallback}), the local NLI model judged instead (stricter: it needs both claims to entail each other)")
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
    g.add_node("web", web_node)
    g.add_node("ground", ground_node)
    g.add_node("judge", judge_node)
    g.add_node("decide", decide_node)
    g.add_edge(START, "read_post")
    g.add_edge("read_post", "extract")
    g.add_conditional_edges("extract", after_extract, ["registry", "official", "web", "ground", END])
    g.add_edge(["registry", *(["official"] if official else []), *(["web"] if web else []), "ground"], "judge")
    g.add_edge("judge", "decide")
    g.add_edge("decide", END)
    return g.compile()
