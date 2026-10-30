"""Tool calling: native function calls, graded by structure (BFCL-style).

Every case offers the same five tools, so a model has to choose, not just
fill in a form. Cases cover picking the right tool, typed and enum arguments,
arrays, using a tool result already in the conversation, and the two
"relevance" cases where the correct move is to call nothing.

This measures exactly what GitPilot's NATIVE dialect needs. A model that
answers in prose instead of emitting a tool call fails the call cases; that is
a capability gap, not a grading quirk (such models run on the REACT_TEXT or
LITE dialects instead).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from ..client import ChatResult
from . import Task, Verdict, call_reply, text_reply, user


def _fn(name: str, description: str, properties: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }}


TOOLS: List[Dict[str, Any]] = [
    _fn("get_weather", "Current weather for a city.",
        {"city": {"type": "string", "description": "City name"},
         "unit": {"type": "string", "enum": ["celsius", "fahrenheit"], "description": "Temperature unit"}},
        ["city"]),
    _fn("convert_currency", "Convert an amount between currencies, using ISO 4217 codes such as EUR or USD.",
        {"amount": {"type": "number"},
         "from_currency": {"type": "string", "description": "ISO 4217 code"},
         "to_currency": {"type": "string", "description": "ISO 4217 code"}},
        ["amount", "from_currency", "to_currency"]),
    _fn("search_code", "Search the repository for a text pattern.",
        {"pattern": {"type": "string"},
         "path": {"type": "string", "description": "Directory to search, relative to the repo root"}},
        ["pattern"]),
    _fn("create_issue", "Open an issue in the issue tracker.",
        {"title": {"type": "string"}, "body": {"type": "string"},
         "labels": {"type": "array", "items": {"type": "string"}}},
        ["title", "body"]),
    _fn("run_tests", "Run the project's test suite.",
        {"path": {"type": "string", "description": "Test file or directory"},
         "verbose": {"type": "boolean"}},
        []),
]


def _norm(value: Any) -> str:
    return str(value).strip().lower().strip("./")


def _expect_call(name: str, check: Callable[[Dict[str, Any]], Optional[str]]) -> Callable[[ChatResult], Verdict]:
    def grade(reply: ChatResult) -> Verdict:
        if not reply.tool_calls:
            return Verdict(False, "answered in text instead of calling a tool")
        call = reply.tool_calls[0]
        if call["name"] != name:
            return Verdict(False, f"called {call['name'] or '?'} instead of {name}")
        args = call["arguments"] if isinstance(call["arguments"], dict) else {}
        if "__unparsed__" in args:
            return Verdict(False, "tool arguments were not valid JSON")
        problem = check(args)
        if problem:
            return Verdict(False, problem)
        return Verdict(True, f"{name} called correctly")
    return grade


def _expect_no_call(must_mention: List[str]) -> Callable[[ChatResult], Verdict]:
    def grade(reply: ChatResult) -> Verdict:
        if reply.tool_calls:
            return Verdict(False, f"called {reply.tool_calls[0]['name']} when no tool was needed")
        text = reply.text.lower()
        missing = [word for word in must_mention if word not in text]
        if missing:
            return Verdict(False, f"answer did not mention {', '.join(missing)}")
        return Verdict(True, "answered without a tool")
    return grade


def _number(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _check_weather(args: Dict[str, Any]) -> Optional[str]:
    if "copenhagen" not in _norm(args.get("city", "")):
        return f"city was {args.get('city')!r}"
    if _norm(args.get("unit", "")) != "fahrenheit":
        return f"unit was {args.get('unit')!r}, expected fahrenheit"
    return None


def _currency(amount: float, src: str, dst: str) -> Callable[[Dict[str, Any]], Optional[str]]:
    def check(args: Dict[str, Any]) -> Optional[str]:
        if _number(args.get("amount")) != amount:
            return f"amount was {args.get('amount')!r}"
        if _norm(args.get("from_currency", "")) != src.lower():
            return f"from_currency was {args.get('from_currency')!r}, expected {src}"
        if _norm(args.get("to_currency", "")) != dst.lower():
            return f"to_currency was {args.get('to_currency')!r}, expected {dst}"
        return None
    return check


def _check_search(args: Dict[str, Any]) -> Optional[str]:
    if "todo" not in _norm(args.get("pattern", "")):
        return f"pattern was {args.get('pattern')!r}"
    if _norm(args.get("path", "")) != "src":
        return f"path was {args.get('path')!r}, expected src"
    return None


def _check_issue(args: Dict[str, Any]) -> Optional[str]:
    if _norm(args.get("title", "")) != "login fails on safari":
        return f"title was {args.get('title')!r}"
    labels = args.get("labels") or []
    if isinstance(labels, str):
        labels = [labels]
    if "bug" not in [_norm(label) for label in labels]:
        return f"labels were {args.get('labels')!r}, expected to include bug"
    if not str(args.get("body", "")).strip():
        return "body was empty"
    return None


def _check_tests(args: Dict[str, Any]) -> Optional[str]:
    if _norm(args.get("path", "")) != "tests/unit":
        return f"path was {args.get('path')!r}, expected tests/unit"
    verbose = args.get("verbose")
    if verbose is not True and _norm(verbose) != "true":
        return f"verbose was {verbose!r}, expected true"
    return None


def _task(task_id: str, category: str, messages: List[Dict[str, Any]],
          grade: Callable[[ChatResult], Verdict], reference: ChatResult) -> Task:
    return Task(id=f"tools/{task_id}", suite="tools", category=category, messages=messages,
                grade=grade, tools=TOOLS, max_tokens=2048, reference=reference)


_WEATHER_HISTORY = [
    {"role": "user", "content": "What's the weather like in Oslo?"},
    {"role": "assistant", "content": "", "tool_calls": [{
        "id": "call_1", "type": "function",
        "function": {"name": "get_weather", "arguments": "{\"city\": \"Oslo\"}"}}]},
    {"role": "tool", "tool_call_id": "call_1", "content": "{\"temp_c\": 9, \"conditions\": \"heavy rain\"}"},
    {"role": "user", "content": "Should I take an umbrella today? Answer yes or no and say why."},
]

SYSTEM = {"role": "system", "content": "You are a developer assistant. Use a tool when it helps; "
                                       "otherwise answer directly."}

TASKS: List[Task] = [
    _task("weather_unit", "arguments",
          [SYSTEM, *user("What's the weather in Copenhagen right now, in fahrenheit?")],
          _expect_call("get_weather", _check_weather),
          call_reply("get_weather", {"city": "Copenhagen", "unit": "fahrenheit"})),
    _task("currency_eur_usd", "arguments",
          [SYSTEM, *user("Convert 250 euros to US dollars.")],
          _expect_call("convert_currency", _currency(250, "EUR", "USD")),
          call_reply("convert_currency", {"amount": 250, "from_currency": "EUR", "to_currency": "USD"})),
    _task("currency_dkk_sek", "selection",
          [SYSTEM, *user("How much is 100 Danish kroner in Swedish kronor?")],
          _expect_call("convert_currency", _currency(100, "DKK", "SEK")),
          call_reply("convert_currency", {"amount": 100, "from_currency": "DKK", "to_currency": "SEK"})),
    _task("search_path", "selection",
          [SYSTEM, *user("Search the src directory for the text TODO.")],
          _expect_call("search_code", _check_search),
          call_reply("search_code", {"pattern": "TODO", "path": "src"})),
    _task("issue_labels", "arguments",
          [SYSTEM, *user("Open an issue titled 'Login fails on Safari' with the label bug, "
                         "and a one-sentence description of the problem.")],
          _expect_call("create_issue", _check_issue),
          call_reply("create_issue", {"title": "Login fails on Safari", "labels": ["bug"],
                                      "body": "Users cannot log in using Safari."})),
    _task("tests_verbose", "arguments",
          [SYSTEM, *user("Run the tests in tests/unit with verbose output.")],
          _expect_call("run_tests", _check_tests),
          call_reply("run_tests", {"path": "tests/unit", "verbose": True})),
    _task("relevance_arithmetic", "relevance",
          [SYSTEM, *user("What is 17 + 25? Just give the number.")],
          _expect_no_call(["42"]),
          text_reply("42")),
    _task("relevance_concept", "relevance",
          [SYSTEM, *user("In one sentence, what does a compiler do?")],
          _expect_no_call(["code"]),
          text_reply("A compiler translates source code into machine code.")),
    _task("use_tool_result", "multi-turn",
          [SYSTEM, *_WEATHER_HISTORY],
          _expect_no_call(["yes", "rain"]),
          text_reply("Yes, it is raining heavily in Oslo, so take an umbrella.")),
]
