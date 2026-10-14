from datetime import UTC, datetime

from botocore.exceptions import ClientError

from shared import llm_cost

HAIKU = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
NOW = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)


def usage(inputs, outputs):
    return {"inputTokens": inputs, "outputTokens": outputs}


def test_cost_is_integer_micro_dollars_from_list_prices():
    # Haiku 4.5: $1 per 1M input tokens, $5 per 1M output tokens.
    assert llm_cost.estimate(HAIKU, usage(2100, 650)) == 2100 + 650 * 5


def test_textract_pages_add_a_cent_each():
    assert llm_cost.estimate(HAIKU, usage(0, 0), textract_pages=3) == 30_000


def test_the_model_is_found_inside_any_inference_profile_id():
    for model_id in (HAIKU, "anthropic.claude-haiku-4-5-20251001-v1:0", "global." + HAIKU[3:]):
        assert llm_cost.estimate(model_id, usage(1, 1)) == 6


def test_an_unknown_model_counts_its_tokens_at_zero_cost(caplog):
    assert llm_cost.estimate("amazon.nova-pro-v1:0", usage(1000, 1000), textract_pages=1) == 10_000
    assert "unpriced_model" in caplog.text


def test_tokens_per_second_uses_the_time_spent_waiting_for_the_model():
    assert llm_cost.tokens_per_second(650, [1800]) == 361
    assert llm_cost.tokens_per_second(1290, [1800, 2500]) == 300  # a repair pass: both calls
    assert llm_cost.tokens_per_second(650, []) is None
    assert llm_cost.tokens_per_second(650, [0]) is None


def test_record_returns_the_log_fields(table):
    fields = llm_cost.record(table, NOW, HAIKU, usage(2100, 650), [1800], textract_pages=1)

    assert fields == {"costMicroUsd": 15_350, "outputTokensPerSec": 361}


def test_record_adds_to_todays_and_this_months_app_totals(table):
    llm_cost.record(table, NOW, HAIKU, usage(2100, 650), [1800], textract_pages=1)
    llm_cost.record(table, NOW, HAIKU, usage(900, 100), [700])

    for period in ("2026-10-14", "2026-10"):
        item = table.get_item(Key={"PK": "GLOBAL", "SK": f"USAGE#{period}"})["Item"]
        assert item["aiReads"] == 2
        assert item["aiCostMicroUsd"] == 15_350 + 1_400
        assert item["inputTokens"] == 3000
        assert item["outputTokens"] == 750
        assert item["expiresAt"] > NOW.timestamp()


def test_a_failed_count_never_fails_the_read(table, monkeypatch):
    def broken(**kwargs):
        raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "Update")

    monkeypatch.setattr(table, "update_item", broken)

    assert llm_cost.record(table, NOW, HAIKU, usage(1, 1), [10])["costMicroUsd"] == 6
