"""Reasoning: short multi-step problems with one integer answer (exact match).

Written fresh, in a software-engineering setting, so they cannot be answered
from a memorised GSM8K item. The model is asked to finish with
``Answer: <number>``; when it does not, the last number in the reply is used
and the verdict says so.
"""
from __future__ import annotations

import re
from typing import List

from ..client import ChatResult
from . import Task, Verdict, text_reply, user

SUFFIX = "Think it through briefly, then finish with a final line of the form 'Answer: <number>'."

_ANSWER = re.compile(r"answer\s*[:=]\s*\**\s*(-?\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def extract_answer(text: str) -> tuple[str, bool]:
    """The final numeric answer, and whether it came from an ``Answer:`` line."""
    marked = _ANSWER.findall(text or "")
    if marked:
        return marked[-1].replace(",", ""), True
    numbers = _NUMBER.findall(text or "")
    return (numbers[-1].replace(",", ""), False) if numbers else ("", False)


def _task(task_id: str, category: str, question: str, answer: int) -> Task:
    def grade(reply: ChatResult) -> Verdict:
        value, marked = extract_answer(reply.text)
        if not value:
            return Verdict(False, "no number in the answer")
        try:
            correct = float(value) == answer
        except ValueError:
            correct = False
        how = "" if marked else " (no 'Answer:' line; used the last number)"
        if correct:
            return Verdict(True, f"answered {answer}{how}")
        return Verdict(False, f"answered {value}, expected {answer}{how}")

    return Task(id=f"reasoning/{task_id}", suite="reasoning", category=category,
                messages=user(f"{question}\n\n{SUFFIX}"), grade=grade, max_tokens=4096,
                reference=text_reply(f"Working it out.\nAnswer: {answer}"), spec={"answer": answer})


TASKS: List[Task] = [
    _task("build_time", "arithmetic",
          "A CI build takes 12 minutes. Caching removes 25% of that time, and a second change "
          "then removes 4 more minutes from the cached build. How many minutes does the build "
          "take now?", 5),
    _task("load_split", "proportion",
          "Server A can handle 120 requests per second and server B 80. Incoming traffic of 1000 "
          "requests per second is split between them in proportion to capacity. How many "
          "requests per second go to server B?", 400),
    _task("release_week", "sequence",
          "A team ships a release every 3 weeks, and the first release is in week 1 (so weeks 1, "
          "4, 7, and so on). In which week is the 10th release?", 28),
    _task("log_rotation", "arithmetic",
          "A log file starts empty at the beginning of day 1 and grows by 15 MB each day. On "
          "which day does it first reach 100 MB or more by the end of that day?", 7),
    _task("mtls_pairs", "combinatorics",
          "Five microservices each need a separate mutual-TLS certificate pair with every other "
          "service, one pair per two services. How many certificate pairs are needed?", 10),
    _task("fibonacci", "recursion",
          "A function is defined by f(1) = 1, f(2) = 1 and f(n) = f(n-1) + f(n-2). What is "
          "f(9)?", 34),
    _task("version_order", "ordering",
          "Sort these versions in ascending semantic-version order: 1.2.0, 1.10.0, 1.9.3, 1.1.5. "
          "At which position (counting from 1) does 1.10.0 end up?", 4),
    _task("retry_budget", "arithmetic",
          "A client retries a failed request with delays of 1, 2, 4 and 8 seconds before giving "
          "up, and each attempt itself takes 3 seconds. The first attempt and all four retries "
          "fail. How many seconds pass from the first attempt starting to the last one ending?", 30),
]
