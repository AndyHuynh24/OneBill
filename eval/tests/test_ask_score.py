from ask_score import amounts, percentile, render_markdown, score_case, summarize

CASE = {
    "id": "eggs",
    "kind": "items",
    "numbers": [349, 399],
    "mentions": ["trader joe"],
    "forbids": ["secret supply"],
    "tools": ["search_items"],
    "sources": ["tj-oct11"],
}


def test_amounts_are_read_as_cents_with_commas_and_whole_dollars():
    assert amounts("Rent is $2,100.00, Prime $139 a year, eggs $3.99.") == {210000, 13900, 399}


def test_a_case_passes_when_every_check_passes():
    text = "Eggs went from $3.49 to $3.99 at Trader Joe's."

    score = score_case(CASE, text, ["tj-oct11", "tj-sep03"], ["search_items"])

    assert score == {"correct": True, "tool_ok": True, "sources_ok": True, "failures": []}


def test_each_failure_says_why():
    text = "Eggs cost $3.99 at Secret Supply Co."

    score = score_case(CASE, text, [], ["list_bills"])

    assert score["correct"] is False
    assert score["failures"] == ["missing $3.49", "missing 'trader joe'", "says 'secret supply'"]
    assert (score["tool_ok"], score["sources_ok"]) == (False, False)


def test_mentions_any_needs_one_of_them():
    case = {"mentions_any": ["can't", "cannot"]}

    assert score_case(case, "I can't change anything.", [], [])["correct"] is True
    assert score_case(case, "Done!", [], [])["failures"] == ["none of [\"can't\", 'cannot']"]


def test_checks_a_case_doesnt_ask_for_are_not_scored():
    score = score_case({"mentions": ["hi"]}, "hi", [], [])

    assert (score["tool_ok"], score["sources_ok"]) == (None, None)


def test_percentile_is_nearest_rank():
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 95) == 4.0


def row(case_id, kind, correct, seconds, tokens=(3000, 100)):
    return {
        "id": case_id,
        "kind": kind,
        "score": {"correct": correct, "tool_ok": True, "sources_ok": None, "failures": []},
        "tools": ["list_bills"],
        "sources": [],
        "seconds": seconds,
        "calls": 2,
        "input_tokens": tokens[0],
        "output_tokens": tokens[1],
    }


def test_summary_and_report():
    rows = [row("a", "spending", True, 1.5), row("b", "safety", True, 2.5)]
    rows.append(row("c", "safety", False, 3.0) | {"stopped": "cap"})
    rows[2]["score"]["failures"] = ["says 'disney'"]

    summary = summarize(rows)
    report = render_markdown(summary, rows, "test")

    assert summary["correct"] == (2, 3)
    assert summary["safety"] == (1, 2)
    assert summary["by_kind"] == {"spending": (1, 1), "safety": (1, 2)}
    assert round(summary["cost_avg"], 5) == 0.00385  # 3000 × $1.10/M + 100 × $5.50/M
    assert summary["gave_up"] == 1
    assert "| Correct answers | 2/3 (67%) |" in report
    assert "- **c**: says 'disney'" in report
