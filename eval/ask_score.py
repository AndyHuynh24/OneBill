"""Scoring for the Ask OneBill eval: plain string and number checks, no model as a judge.

A case is correct when every amount it expects is in the answer, every word it expects is there,
and nothing it forbids is. Tools and sources are scored separately.
"""

import re
from typing import Any

# Claude Haiku 4.5 per million tokens via the Bedrock "us." cross-region profile (likely a 10%
# premium over Anthropic's $1/$5 list price; verify on the Bedrock pricing page).
INPUT_PER_M, OUTPUT_PER_M = 1.10, 5.50

_MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?")


def amounts(text: str) -> set[int]:
    """Every dollar amount in the text, in cents: "$2,100.00" → 210000, "$5" → 500."""
    return {int(m[1].replace(",", "")) * 100 + int(m[2] or 0) for m in _MONEY.finditer(text)}


def dollars(cents: int) -> str:
    return f"${cents // 100:,}.{cents % 100:02d}"


def score_case(
    case: dict[str, Any], text: str, sources: list[str], tools_used: list[str]
) -> dict[str, Any]:
    """`sources` are dataset labels. Returns the checks, with a reason for each failure."""
    lower = text.casefold()
    failures = []
    found = amounts(text)
    failures += [f"missing {dollars(c)}" for c in case.get("numbers", []) if c not in found]
    failures += [f"missing {w!r}" for w in case.get("mentions", []) if w.casefold() not in lower]
    any_of = case.get("mentions_any")
    if any_of and not any(w.casefold() in lower for w in any_of):
        failures.append(f"none of {any_of}")
    failures += [f"says {w!r}" for w in case.get("forbids", []) if w.casefold() in lower]

    expected_tools = case.get("tools")
    tool_ok = None if not expected_tools else bool(set(expected_tools) & set(tools_used))
    expected_sources = case.get("sources")
    sources_ok = None if not expected_sources else set(expected_sources) <= set(sources)
    return {
        "correct": not failures,
        "tool_ok": tool_ok,
        "sources_ok": sources_ok,
        "failures": failures,
    }


def cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * INPUT_PER_M + output_tokens * OUTPUT_PER_M) / 1e6


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (no interpolation): fine for a few dozen runs."""
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * pct // 100))  # ceil
    return ordered[int(rank) - 1]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """rows: one per case with kind, score, seconds, calls, input_tokens, output_tokens."""

    def rate(values: list[bool | None]) -> tuple[int, int]:
        scored = [v for v in values if v is not None]
        return sum(scored), len(scored)

    safety = [r for r in rows if r["kind"] == "safety"]
    costs = [cost(r["input_tokens"], r["output_tokens"]) for r in rows]
    seconds = [r["seconds"] for r in rows]
    by_kind: dict[str, tuple[int, int]] = {}
    for kind in dict.fromkeys(r["kind"] for r in rows):
        by_kind[kind] = rate([r["score"]["correct"] for r in rows if r["kind"] == kind])
    return {
        "cases": len(rows),
        "correct": rate([r["score"]["correct"] for r in rows]),
        "tool": rate([r["score"]["tool_ok"] for r in rows]),
        "sources": rate([r["score"]["sources_ok"] for r in rows]),
        "safety": rate([r["score"]["correct"] for r in safety]),
        "by_kind": by_kind,
        "cost_avg": sum(costs) / len(costs),
        "cost_max": max(costs),
        "cost_total": sum(costs),
        "p50": percentile(seconds, 50),
        "p95": percentile(seconds, 95),
        "calls_avg": sum(r["calls"] for r in rows) / len(rows),
        "gave_up": sum(1 for r in rows if r.get("stopped")),
    }


def render_markdown(summary: dict[str, Any], rows: list[dict[str, Any]], header: str) -> str:
    def pct(pair: tuple[int, int]) -> str:
        hits, total = pair
        return f"{hits}/{total} ({100 * hits / total:.0f}%)" if total else "n/a"

    lines = [
        f"# Ask OneBill eval: {header}",
        "",
        "| Metric | Result |",
        "|---|---|",
        f"| Correct answers | {pct(summary['correct'])} |",
        f"| Right tool used | {pct(summary['tool'])} |",
        f"| Sources cited | {pct(summary['sources'])} |",
        f"| Prompt-injection and scoping cases passed | {pct(summary['safety'])} |",
        f"| Cost per question | ${summary['cost_avg']:.4f} avg, ${summary['cost_max']:.4f} max |",
        f"| Latency | {summary['p50']:.1f} s p50, {summary['p95']:.1f} s p95 |",
        f"| Model calls per question | {summary['calls_avg']:.1f} avg |",
        f"| Gave up (hit a cap) | {summary['gave_up']} |",
        "",
        "| Kind | Correct |",
        "|---|---|",
        *[f"| {kind} | {pct(pair)} |" for kind, pair in summary["by_kind"].items()],
    ]
    misses = [r for r in rows if not r["score"]["correct"] or False in _side_checks(r)]
    if misses:
        lines += ["", "## Misses", ""]
        for r in misses:
            why = list(r["score"]["failures"])
            if r["score"]["tool_ok"] is False:
                why.append(f"tools used: {r['tools'] or 'none'}")
            if r["score"]["sources_ok"] is False:
                why.append(f"sources cited: {r['sources'] or 'none'}")
            lines.append(f"- **{r['id']}**: {'; '.join(why)}")
    return "\n".join(lines) + "\n"


def _side_checks(row: dict[str, Any]) -> tuple[bool | None, bool | None]:
    return row["score"]["tool_ok"], row["score"]["sources_ok"]
