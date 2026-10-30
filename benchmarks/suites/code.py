"""Coding: write a small function, graded by hidden unit tests (pass@1).

Fresh tasks rather than HumanEval or MBPP items, so a model cannot pass by
having memorised a public benchmark. Each task ships a reference solution; the
test suite proves the reference passes and a plausible wrong answer fails.
"""
from __future__ import annotations

from typing import List

from ..client import ChatResult
from ..sandbox import extract_code, run_tests
from . import Task, Verdict, text_reply, user

INSTRUCTIONS = (
    "Return only the code in a single ```python block. Use only the Python standard "
    "library, do not read files or use the network, and do not include tests, "
    "example usage or explanations."
)


def _task(task_id: str, category: str, prompt: str, tests: str, reference: str) -> Task:
    def grade(reply: ChatResult) -> Verdict:
        code = extract_code(reply.text)
        if not code:
            return Verdict(False, "no code in the reply")
        result = run_tests(code, tests)
        return Verdict(result.passed, result.detail)

    return Task(
        id=f"code/{task_id}",
        suite="code",
        category=category,
        messages=user(f"{prompt}\n\n{INSTRUCTIONS}"),
        grade=grade,
        max_tokens=4096,  # room for reasoning models to think, then answer
        reference=text_reply(f"```python\n{reference.strip()}\n```"),
        spec={"tests": tests},
    )


TASKS: List[Task] = [
    _task(
        "normalize_whitespace", "strings",
        "Write a Python function `normalize_whitespace(s: str) -> str` that collapses every "
        "run of whitespace (spaces, tabs, newlines) into a single space and removes leading "
        "and trailing whitespace.",
        tests="""
assert normalize_whitespace("  a   b\\t\\tc\\n d ") == "a b c d"
assert normalize_whitespace("") == ""
assert normalize_whitespace(" \\n\\t ") == ""
assert normalize_whitespace("single") == "single"
""",
        reference="""
def normalize_whitespace(s: str) -> str:
    return " ".join(s.split())
""",
    ),
    _task(
        "run_length_encode", "strings",
        "Write a Python function `run_length_encode(s: str) -> str` that run-length encodes a "
        "string as character followed by count, for example \"aaabcc\" becomes \"a3b1c2\". "
        "An empty string encodes to an empty string.",
        tests="""
assert run_length_encode("aaabcc") == "a3b1c2"
assert run_length_encode("") == ""
assert run_length_encode("a") == "a1"
assert run_length_encode("aabbaa") == "a2b2a2"
assert run_length_encode("zzzzzzzzzzzz") == "z12"
""",
        reference="""
def run_length_encode(s: str) -> str:
    out = []
    i = 0
    while i < len(s):
        j = i
        while j < len(s) and s[j] == s[i]:
            j += 1
        out.append(f"{s[i]}{j - i}")
        i = j
    return "".join(out)
""",
    ),
    _task(
        "merge_intervals", "algorithms",
        "Write a Python function `merge_intervals(intervals: list[list[int]]) -> list[list[int]]` "
        "that merges overlapping or touching closed intervals and returns them sorted by start. "
        "For example [[1,3],[2,6],[8,10],[10,12]] becomes [[1,6],[8,12]]. Do not modify the input.",
        tests="""
data = [[8, 10], [1, 3], [2, 6], [10, 12]]
assert merge_intervals(data) == [[1, 6], [8, 12]]
assert data == [[8, 10], [1, 3], [2, 6], [10, 12]]
assert merge_intervals([]) == []
assert merge_intervals([[5, 5]]) == [[5, 5]]
assert merge_intervals([[1, 4], [2, 3]]) == [[1, 4]]
""",
        reference="""
def merge_intervals(intervals):
    merged = []
    for start, end in sorted([list(i) for i in intervals]):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged
""",
    ),
    _task(
        "parse_semver", "parsing",
        "Write a Python function `parse_semver(version: str) -> tuple[int, int, int]` that parses "
        "a semantic version such as \"1.4.2\" into (1, 4, 2). Accept an optional leading \"v\" and "
        "ignore any pre-release or build suffix after \"-\" or \"+\" (\"v2.0.1-rc.1\" gives (2, 0, 1)). "
        "Raise ValueError for anything that is not three dot-separated non-negative integers.",
        tests="""
assert parse_semver("1.4.2") == (1, 4, 2)
assert parse_semver("v2.0.1-rc.1") == (2, 0, 1)
assert parse_semver("10.20.30+build.5") == (10, 20, 30)
for bad in ["1.2", "1.2.x", "", "a.b.c", "1.2.3.4", "-1.2.3"]:
    try:
        parse_semver(bad)
    except ValueError:
        pass
    else:
        raise AssertionError(f"accepted {bad!r}")
""",
        reference="""
import re

def parse_semver(version: str):
    match = re.fullmatch(r"v?(\\d+)\\.(\\d+)\\.(\\d+)(?:[-+].*)?", version)
    if not match:
        raise ValueError(version)
    return tuple(int(part) for part in match.groups())
""",
    ),
    _task(
        "top_k_words", "data",
        "Write a Python function `top_k_words(text: str, k: int) -> list[tuple[str, int]]` that "
        "returns the k most frequent words with their counts. Words are maximal runs of letters "
        "a-z, compared case-insensitively and returned in lowercase. Order by count descending, "
        "then alphabetically. If there are fewer than k distinct words, return them all.",
        tests="""
text = "The cat and the hat. THE end, and THE cat!"
assert top_k_words(text, 2) == [("the", 4), ("and", 2)]
assert top_k_words(text, 3) == [("the", 4), ("and", 2), ("cat", 2)]
assert top_k_words("", 3) == []
assert top_k_words("b a c", 5) == [("a", 1), ("b", 1), ("c", 1)]
""",
        reference="""
import re
from collections import Counter

def top_k_words(text, k):
    counts = Counter(re.findall(r"[a-z]+", text.lower()))
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
""",
    ),
    _task(
        "flatten", "data",
        "Write a Python function `flatten(nested: list) -> list` that flattens arbitrarily nested "
        "lists into one flat list, preserving order. Only lists are flattened; strings, tuples "
        "and other values are kept as single items.",
        tests="""
assert flatten([1, [2, [3, [4]], 5], [], [[6]]]) == [1, 2, 3, 4, 5, 6]
assert flatten([]) == []
assert flatten(["ab", ("c", "d"), ["e"]]) == ["ab", ("c", "d"), "e"]
deep = [1]
for _ in range(50):
    deep = [deep]
assert flatten(deep) == [1]
""",
        reference="""
def flatten(nested):
    out = []
    stack = [iter(nested)]
    while stack:
        for item in stack[-1]:
            if isinstance(item, list):
                stack.append(iter(item))
                break
            out.append(item)
        else:
            stack.pop()
    return out
""",
    ),
    _task(
        "balanced_brackets", "parsing",
        "Write a Python function `is_balanced(s: str) -> bool` that returns True when every "
        "(), [] and {} bracket in the string is correctly matched and nested. Other characters "
        "are ignored.",
        tests="""
assert is_balanced("f(a[1]) { return [x]; }")
assert is_balanced("")
assert not is_balanced("(]")
assert not is_balanced("([)]")
assert not is_balanced("((")
assert not is_balanced("}")
""",
        reference="""
def is_balanced(s):
    pairs = {")": "(", "]": "[", "}": "{"}
    stack = []
    for ch in s:
        if ch in "([{":
            stack.append(ch)
        elif ch in pairs:
            if not stack or stack.pop() != pairs[ch]:
                return False
    return not stack
""",
    ),
    _task(
        "rotate_matrix", "algorithms",
        "Write a Python function `rotate_clockwise(matrix: list[list[int]]) -> list[list[int]]` "
        "that returns a new matrix rotated 90 degrees clockwise. The matrix may be rectangular. "
        "Do not modify the input.",
        tests="""
m = [[1, 2, 3], [4, 5, 6]]
assert rotate_clockwise(m) == [[4, 1], [5, 2], [6, 3]]
assert m == [[1, 2, 3], [4, 5, 6]]
assert rotate_clockwise([[1]]) == [[1]]
assert rotate_clockwise([[1, 2], [3, 4]]) == [[3, 1], [4, 2]]
assert rotate_clockwise([]) == []
""",
        reference="""
def rotate_clockwise(matrix):
    return [list(row) for row in zip(*matrix[::-1])]
""",
    ),
    _task(
        "roman_to_int", "parsing",
        "Write a Python function `roman_to_int(s: str) -> int` that converts a valid Roman "
        "numeral between 1 and 3999 (using subtractive notation such as IV, IX, XL, XC, CD, CM) "
        "to an integer.",
        tests="""
assert roman_to_int("III") == 3
assert roman_to_int("IV") == 4
assert roman_to_int("IX") == 9
assert roman_to_int("LVIII") == 58
assert roman_to_int("MCMXCIV") == 1994
assert roman_to_int("MMMCMXCIX") == 3999
""",
        reference="""
def roman_to_int(s):
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    for i, ch in enumerate(s):
        value = values[ch]
        if i + 1 < len(s) and values[s[i + 1]] > value:
            total -= value
        else:
            total += value
    return total
""",
    ),
    _task(
        "lru_cache", "classes",
        "Write a Python class `LRUCache` with `__init__(self, capacity: int)`, `get(self, key)` "
        "returning the value or -1 when missing, and `put(self, key, value)`. When the cache is "
        "full, `put` of a new key evicts the least recently used key; both `get` and `put` count "
        "as a use.",
        tests="""
c = LRUCache(2)
c.put(1, "a")
c.put(2, "b")
assert c.get(1) == "a"
c.put(3, "c")
assert c.get(2) == -1
assert c.get(3) == "c"
c.put(1, "A")
c.put(4, "d")
assert c.get(3) == -1
assert c.get(1) == "A"
assert c.get(4) == "d"
""",
        reference="""
from collections import OrderedDict

class LRUCache:
    def __init__(self, capacity):
        self.capacity = capacity
        self.data = OrderedDict()

    def get(self, key):
        if key not in self.data:
            return -1
        self.data.move_to_end(key)
        return self.data[key]

    def put(self, key, value):
        if key in self.data:
            self.data.move_to_end(key)
        self.data[key] = value
        if len(self.data) > self.capacity:
            self.data.popitem(last=False)
""",
    ),
]
