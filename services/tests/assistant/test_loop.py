"""The agent loop (ReAct): model → tool → result → model … → answer, with hard caps."""

from datetime import date

import boto3
import pytest
from botocore.stub import ANY, Stubber

from assistant import loop, tools

MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
TODAY = date(2026, 10, 14)


@pytest.fixture
def bedrock():
    client = boto3.client("bedrock-runtime", region_name="us-east-1")
    with Stubber(client) as stub:
        yield client, stub
        stub.assert_no_pending_responses()


def reply(text, tokens=(100, 20)):
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": tokens[0], "outputTokens": tokens[1], "totalTokens": sum(tokens)},
        "metrics": {"latencyMs": 1},
    }


def tool_call(name, args, use_id="t1"):
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"toolUse": {"toolUseId": use_id, "name": name, "input": args}}],
            }
        },
        "stopReason": "tool_use",
        "usage": {"inputTokens": 200, "outputTokens": 30, "totalTokens": 230},
        "metrics": {"latencyMs": 1},
    }


PARAMS = {
    "modelId": MODEL,
    "system": ANY,
    "messages": ANY,
    "toolConfig": ANY,
    "inferenceConfig": ANY,
}


def bill(table, doc_id, vendor, day, total, user="user-a"):
    table.put_item(
        Item={
            "PK": f"USER#{user}",
            "SK": f"DOC#{doc_id}",
            "docId": doc_id,
            "status": "ready",
            "vendor": vendor,
            "documentDate": day,
            "totalCents": total,
            "currency": "USD",
        }
    )


def ask(client, table, question, history=(), **kwargs):
    ctx = tools.Context(table=table, user_id="user-a", today=TODAY)
    return loop.ask(client, MODEL, ctx, question, list(history), **kwargs)


def test_a_question_that_needs_no_tool(table, bedrock):
    client, stub = bedrock
    stub.add_response("converse", reply("Hi! Ask me about your bills."), PARAMS)

    answer = ask(client, table, "hello")

    assert answer.text == "Hi! Ask me about your bills."
    assert answer.sources == []
    assert (answer.calls, answer.tool_calls, answer.input_tokens) == (1, 0, 100)


def test_the_model_uses_a_tool_and_answers_from_its_numbers(table, bedrock):
    bill(table, "d1", "TRADER JOE'S", "2026-09-03", 4210)
    bill(table, "d2", "TRADER JOE'S", "2026-09-20", 1890)
    client, stub = bedrock
    sent = []
    client.meta.events.register(
        "before-parameter-build.bedrock-runtime.Converse", lambda params, **_: sent.append(params)
    )
    stub.add_response("converse", tool_call("list_bills", {"vendor": "trader joe"}), PARAMS)
    stub.add_response(
        "converse",
        reply("You spent $61.00 at Trader Joe's in September.\nSOURCES: d1, d2, d999"),
        PARAMS,
    )

    answer = ask(client, table, "How much at Trader Joe's in September?")

    assert answer.text == "You spent $61.00 at Trader Joe's in September."
    assert answer.sources == ["d1", "d2"]  # an id the tools never returned is dropped
    assert (answer.calls, answer.tool_calls) == (2, 1)
    result = sent[1]["messages"][-1]["content"][0]["toolResult"]
    assert result["toolUseId"] == "t1"
    assert result["content"][0]["json"]["totalCents"] == 6100  # computed by code, not the model


def test_conversation_history_goes_before_the_question(table, bedrock):
    client, stub = bedrock
    sent = []
    client.meta.events.register(
        "before-parameter-build.bedrock-runtime.Converse", lambda params, **_: sent.append(params)
    )
    stub.add_response("converse", reply("Minh owes you $40.00."), PARAMS)
    history = [
        {"role": "user", "text": "Who owes me?"},
        {"role": "assistant", "text": "Minh and Linh."},
    ]

    ask(client, table, "How much does Minh owe?", history)

    roles = [m["role"] for m in sent[0]["messages"]]
    assert roles == ["user", "assistant", "user"]
    assert sent[0]["messages"][-1]["content"][0]["text"] == "How much does Minh owe?"


def test_it_stops_after_four_model_calls(table, bedrock):
    client, stub = bedrock
    for i in range(4):
        stub.add_response("converse", tool_call("list_bills", {}, use_id=f"t{i}"), PARAMS)

    answer = ask(client, table, "Loop forever")

    assert answer.calls == 4
    assert answer.stopped == "cap"
    assert "couldn't finish" in answer.text


def test_it_stops_before_running_out_of_time(table, bedrock):
    client, stub = bedrock
    stub.add_response("converse", tool_call("list_bills", {}), PARAMS)
    ticks = iter([0.0, 0.0] + [30.0] * 10)  # the 2nd call would start 30 s in

    answer = ask(client, table, "Slow", clock=lambda: next(ticks))

    assert answer.calls == 1
    assert answer.stopped == "time"


def test_a_tool_error_goes_back_to_the_model_to_fix(table, bedrock):
    client, stub = bedrock
    sent = []
    client.meta.events.register(
        "before-parameter-build.bedrock-runtime.Converse", lambda params, **_: sent.append(params)
    )
    stub.add_response("converse", tool_call("list_bills", {"from": "last month"}), PARAMS)
    stub.add_response("converse", reply("Which month do you mean?"), PARAMS)

    answer = ask(client, table, "Spending last month?")

    result = sent[1]["messages"][-1]["content"][0]["toolResult"]
    assert "error" in result["content"][0]["json"]
    assert answer.text == "Which month do you mean?"


def test_the_question_and_history_are_redacted_before_the_model_sees_them(table, bedrock):
    client, stub = bedrock
    sent = []
    client.meta.events.register(
        "before-parameter-build.bedrock-runtime.Converse", lambda params, **_: sent.append(params)
    )
    stub.add_response("converse", reply("Noted."), PARAMS)
    history = [
        {"role": "user", "text": "my email is jane@example.com"},
        {"role": "assistant", "text": "ok"},
    ]

    ask(client, table, "card 4111 1111 1111 1111?", history)

    text = str(sent[0]["messages"])
    assert "jane@example.com" not in text
    assert "4111 1111 1111 1111" not in text


def test_tool_calls_are_capped_per_step(table, bedrock):
    client, stub = bedrock
    sent = []
    client.meta.events.register(
        "before-parameter-build.bedrock-runtime.Converse", lambda params, **_: sent.append(params)
    )
    many = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {"toolUse": {"toolUseId": f"t{i}", "name": "list_bills", "input": {}}}
                    for i in range(5)
                ],
            }
        },
        "stopReason": "tool_use",
        "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
        "metrics": {"latencyMs": 1},
    }
    stub.add_response("converse", many, PARAMS)
    stub.add_response("converse", reply("Done."), PARAMS)

    answer = ask(client, table, "Everything?")

    results = [b["toolResult"] for b in sent[1]["messages"][-1]["content"]]
    assert answer.tool_calls == 3  # the rest are answered with an error, not run
    assert [("error" in r["content"][0]["json"]) for r in results] == [False] * 3 + [True] * 2


def test_a_tool_result_too_big_to_send_asks_for_a_narrower_question(table, bedrock, monkeypatch):
    client, stub = bedrock
    sent = []
    client.meta.events.register(
        "before-parameter-build.bedrock-runtime.Converse", lambda params, **_: sent.append(params)
    )
    monkeypatch.setattr(loop.tools, "run", lambda *_: {"bills": ["x" * loop.MAX_RESULT_CHARS]})
    stub.add_response("converse", tool_call("list_bills", {}), PARAMS)
    stub.add_response("converse", reply("Which month?"), PARAMS)

    ask(client, table, "Everything I ever bought?")

    result = sent[1]["messages"][-1]["content"][0]["toolResult"]
    assert "narrower" in result["content"][0]["json"]["error"]
