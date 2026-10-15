"""The agent loop (the ReAct pattern): the model reasons, calls a tool, reads the result, and
repeats until it can answer. Written by hand, so every step is visible, capped, and testable.

Caps (security and cost reviews): 4 model calls, 400 output tokens each, 3 tool calls per step
and 8 per question, and no new model call after 15 s (each may take 12 s; the Lambda stops at 29
s). The question and history are redacted before the model sees them. Numbers come from tools;
the model only words the answer and names its sources.
"""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from assistant import tools
from assistant.prompt import system_prompt
from shared.pii import redact

MAX_CALLS = 4
LAST_CALL_START = 15.0  # seconds in: a call started later could outlast the Lambda
MAX_OUTPUT_TOKENS = 400
MAX_TOOLS_PER_STEP = 3
MAX_TOOLS = 8
TOOL_LIMIT = {"error": "Tool limit reached. Answer with what you already have."}
GAVE_UP = "I couldn't finish that one. Try a narrower question, like a single month or store."
_SOURCES = re.compile(r"^\s*SOURCES:\s*(?P<ids>.*)$", re.IGNORECASE | re.MULTILINE)


@dataclass
class Answer:
    text: str
    sources: list[str] = field(default_factory=list)
    calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    stopped: str | None = None  # "cap" or "time" when it gave up


def ask(
    client: Any,
    model_id: str,
    ctx: tools.Context,
    question: str,
    history: list[dict[str, str]],
    *,
    clock: Callable[[], float] = time.monotonic,
) -> Answer:
    started = clock()
    messages: list[dict[str, Any]] = [
        {"role": turn["role"], "content": [{"text": redact(turn["text"]).text}]} for turn in history
    ]
    messages.append({"role": "user", "content": [{"text": redact(question).text}]})
    answer = Answer(text=GAVE_UP)
    seen_ids: list[str] = []  # docIds the tools returned: the only valid sources

    while answer.calls < MAX_CALLS:
        if clock() - started >= LAST_CALL_START:
            answer.stopped = "time"
            return answer
        response = client.converse(
            modelId=model_id,
            system=[{"text": system_prompt(ctx.today)}],
            messages=list(messages),  # a snapshot: the loop keeps appending
            toolConfig={"tools": [{"toolSpec": spec} for spec in tools.SPECS]},
            inferenceConfig={"maxTokens": MAX_OUTPUT_TOKENS, "temperature": 0},
        )
        answer.calls += 1
        usage = response.get("usage", {})
        answer.input_tokens += int(usage.get("inputTokens", 0))
        answer.output_tokens += int(usage.get("outputTokens", 0))
        message = response["output"]["message"]
        messages.append(message)

        if response.get("stopReason") != "tool_use":
            text = "".join(block.get("text", "") for block in message["content"]).strip()
            answer.text, answer.sources = _split_sources(text, seen_ids)
            return answer

        results = []
        this_step = 0
        for block in message["content"]:
            use = block.get("toolUse")
            if not use:
                continue
            if this_step >= MAX_TOOLS_PER_STEP or answer.tool_calls >= MAX_TOOLS:
                result = TOOL_LIMIT  # every toolUse still needs a result; this one isn't run
            else:
                this_step += 1
                answer.tool_calls += 1
                result = tools.run(ctx, use["name"], use.get("input") or {})
            seen_ids.extend(_doc_ids(result))
            results.append(
                {
                    "toolResult": {
                        "toolUseId": use["toolUseId"],
                        "content": [{"json": result}],
                        **({"status": "error"} if "error" in result else {}),
                    }
                }
            )
        messages.append({"role": "user", "content": results})

    answer.stopped = "cap"
    return answer


def _split_sources(text: str, seen: list[str]) -> tuple[str, list[str]]:
    """Take the SOURCES line off the answer; keep only ids the tools actually returned."""
    match = _SOURCES.search(text)
    if not match:
        return text, []
    named = [i.strip() for i in match["ids"].split(",") if i.strip()]
    valid = list(dict.fromkeys(i for i in named if i in seen))
    return _SOURCES.sub("", text).strip(), valid[:5]


def _doc_ids(value: Any) -> list[str]:
    if isinstance(value, dict):
        own = [value["docId"]] if isinstance(value.get("docId"), str) else []
        return own + [i for v in value.values() for i in _doc_ids(v)]
    if isinstance(value, list):
        return [i for v in value for i in _doc_ids(v)]
    return []
