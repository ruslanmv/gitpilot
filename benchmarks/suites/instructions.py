"""Instruction following: verifiable constraints (IFEval-style), strict scoring.

Every prompt carries constraints a program can check: counts, formats, casing,
exact phrases. A task passes only when every constraint holds; the detail names
the first one that did not. Agents depend on exactly this — a model that wraps
the requested JSON in a paragraph breaks the tool that parses it.
"""
from __future__ import annotations

import json
import re
from typing import Callable, List, Optional

from ..client import ChatResult
from . import Task, Verdict, text_reply, user

Check = Callable[[str], Optional[str]]


def _task(task_id: str, category: str, prompt: str, checks: List[Check], reference: str) -> Task:
    def grade(reply: ChatResult) -> Verdict:
        text = reply.text.strip()
        if not text:
            return Verdict(False, "empty answer")
        for check in checks:
            problem = check(text)
            if problem:
                return Verdict(False, problem)
        return Verdict(True, f"all {len(checks)} constraints met")

    return Task(id=f"instructions/{task_id}", suite="instructions", category=category,
                messages=user(prompt), grade=grade, max_tokens=2048, reference=text_reply(reference))


def _lines(text: str) -> List[str]:
    return [line for line in text.splitlines() if line.strip()]


def _words(text: str) -> List[str]:
    return re.findall(r"[A-Za-z0-9']+", text)


def exactly_bullets(n: int) -> Check:
    def check(text: str) -> Optional[str]:
        lines = _lines(text)
        bullets = [line for line in lines if line.lstrip().startswith("- ")]
        if len(bullets) != n:
            return f"{len(bullets)} '- ' bullets, expected {n}"
        if len(lines) != n:
            return "text outside the bullet list"
        return None
    return check


def fewer_words_than(n: int) -> Check:
    def check(text: str) -> Optional[str]:
        count = len(_words(text))
        return None if count < n else f"{count} words, limit was under {n}"
    return check


def no_uppercase(text: str) -> Optional[str]:
    found = [ch for ch in text if ch.isupper()]
    return f"uppercase letters present ({''.join(found[:5])})" if found else None


def no_commas(text: str) -> Optional[str]:
    return "contains a comma" if "," in text else None


def line_count(n: int) -> Check:
    def check(text: str) -> Optional[str]:
        count = len(_lines(text))
        return None if count == n else f"{count} lines, expected {n}"
    return check


def mentions(*needles: str) -> Check:
    def check(text: str) -> Optional[str]:
        lowered = text.lower()
        missing = [needle for needle in needles if needle not in lowered]
        return f"does not mention {', '.join(missing)}" if missing else None
    return check


def json_object(keys: List[str], year: int) -> Check:
    def check(text: str) -> Optional[str]:
        body = text.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", body, re.DOTALL)
        if fenced:
            body = fenced.group(1)  # a fenced block is tolerated; prose around it is not
        try:
            data = json.loads(body)
        except ValueError:
            return "not a JSON object on its own"
        if not isinstance(data, dict):
            return "JSON is not an object"
        if sorted(data) != sorted(keys):
            return f"keys were {sorted(data)}, expected {sorted(keys)}"
        if str(data.get("year")).strip() != str(year):
            return f"year was {data.get('year')!r}, expected {year}"
        return None
    return check


def single_word(expected: str) -> Check:
    def check(text: str) -> Optional[str]:
        words = _words(text)
        if len(words) != 1:
            return f"{len(words)} words, expected exactly one"
        return None if words[0].lower() == expected else f"answered {words[0]!r}"
    return check


def angle_summary(max_chars: int) -> Check:
    def check(text: str) -> Optional[str]:
        match = re.search(r"<<(.+?)>>", text)
        if not match:
            return "no <<summary>> found"
        summary = match.group(1).strip()
        return None if len(summary) < max_chars else f"summary is {len(summary)} characters"
    return check


def ends_with(phrase: str) -> Check:
    def check(text: str) -> Optional[str]:
        return None if text.rstrip().endswith(phrase) else f"does not end with {phrase!r}"
    return check


TASKS: List[Task] = [
    _task("three_bullets", "format",
          "List three benefits of code review. Use exactly three bullets that start with '- ' "
          "and write nothing else.",
          [exactly_bullets(3)],
          "- Catches bugs early\n- Spreads knowledge\n- Keeps the style consistent"),
    _task("word_limit", "length",
          "Explain what a merge conflict is in fewer than 40 words.",
          [fewer_words_than(40), mentions("merge")],
          "A merge conflict happens when two branches change the same lines and Git cannot "
          "merge them automatically."),
    _task("lowercase", "casing",
          "Describe what git rebase does in one sentence, using only lowercase letters.",
          [no_uppercase, mentions("commit")],
          "git rebase moves your commits onto a new base commit, replaying them one by one."),
    _task("json_only", "format",
          "Return only a JSON object with exactly the keys \"language\" and \"year\" describing "
          "the Python programming language and the year it was first released. No other text.",
          [json_object(["language", "year"], 1991)],
          "{\"language\": \"Python\", \"year\": 1991}"),
    _task("haiku_no_commas", "format",
          "Write a haiku about debugging: exactly three lines, and do not use any commas.",
          [line_count(3), no_commas],
          "silent stack trace waits\nprint statements bloom like moss\nthe bug was a typo"),
    _task("one_word", "length",
          "Answer with exactly one word: which data structure is first-in, first-out?",
          [single_word("queue")],
          "Queue"),
    _task("commit_summary", "format",
          "Write a commit message for adding input validation to the signup form. Put the summary "
          "line inside double angular brackets, like <<summary>>, and keep the summary under 50 "
          "characters.",
          [angle_summary(50)],
          "<<Validate signup form input>>\n\nReject empty emails and short passwords."),
    _task("ends_with_phrase", "format",
          "Explain what CI stands for in software development, and end your reply with the exact "
          "phrase \"Is there anything else?\"",
          [mentions("continuous integration"), ends_with("Is there anything else?")],
          "CI stands for continuous integration: every change is built and tested automatically. "
          "Is there anything else?"),
]
