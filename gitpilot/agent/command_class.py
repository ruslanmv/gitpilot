# gitpilot/agent/command_class.py
"""Semantic command classification — Batch V4-D2.

`terminal.run("rm -rf build")` and `terminal.run("pytest")` are the same tool
call. Judging them by tool name gives one verdict for both, which is why the
approval card has always had to say "run a shell command" and leave the user to
read the string. This module reads the string instead.

The shape of it (§8.3):

* split on `&&`, `||`, `;`, `|` and newlines, because a chain is only as safe as
  its worst segment;
* `shlex` each segment and classify `argv[0]` (plus the arguments that change the
  answer — `git push` is not `git status`, `sed -i` is not `sed`);
* unknown commands are **MUTATING**, so an unrecognised binary asks rather than
  runs;
* the verdict is **escalate-only** — see :class:`CommandVerdict`. Classification
  can raise a call's risk but is never allowed to lower it, so a command string
  is not a channel for arguing down a tool's declared risk class. The sandbox
  jail, not this table, is the containment boundary.

`shell_safety.BLOCKED_PATTERNS` is consumed rather than duplicated: the substring
denylist that Batch V4-0C unified is checked first, and everything it catches is
DESTRUCTIVE. Docker gets rows here because §7.2 deferred the `docker.*` namespace
but not the ability to type `docker` into a shell.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from ..shell_safety import blocked_reason
from ..toolkit.registry import Risk

# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------

READ_ONLY = "READ_ONLY"
TEST = "TEST"
BUILD = "BUILD"
MUTATING = "MUTATING"
GIT_MUTATION = "GIT_MUTATION"
REMOTE_MUTATION = "REMOTE_MUTATION"
NETWORK = "NETWORK"
DESTRUCTIVE = "DESTRUCTIVE"
PRIVILEGED = "PRIVILEGED"

#: Severity order.  A chain takes the class of its worst segment.
_SEVERITY: Dict[str, int] = {
    READ_ONLY: 0, TEST: 1, BUILD: 2, MUTATING: 3, GIT_MUTATION: 4,
    NETWORK: 5, REMOTE_MUTATION: 6, DESTRUCTIVE: 7, PRIVILEGED: 8,
}

#: What each class means for a user, in the words the approval card uses.
DESCRIPTIONS: Dict[str, str] = {
    READ_ONLY: "reads files or repository state",
    TEST: "runs the test suite",
    BUILD: "builds the project",
    MUTATING: "changes files or installed packages",
    GIT_MUTATION: "changes local git state",
    REMOTE_MUTATION: "publishes to a remote",
    NETWORK: "accesses the network",
    DESTRUCTIVE: "destroys data irrecoverably",
    PRIVILEGED: "requires elevated privileges",
}


@dataclass(frozen=True)
class CommandVerdict:
    """A classification, as the policy pipeline consumes it."""

    command_class: str
    verdict: str            # allow | ask | deny
    reason: str
    risk: str

    @property
    def description(self) -> str:
        return DESCRIPTIONS.get(self.command_class, "does something unrecognised")


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

_READ_ONLY_COMMANDS = frozenset({
    "ls", "cat", "head", "tail", "less", "more", "wc", "grep", "egrep", "fgrep",
    "rg", "ag", "ack", "find", "fd", "file", "stat", "du", "df", "tree",
    "which", "whereis", "type", "pwd", "whoami", "id", "date", "uname", "env",
    "printenv", "echo", "true", "false", "sort", "uniq", "cut", "tr", "diff",
    "cmp", "md5sum", "sha256sum", "basename", "dirname", "realpath", "readlink",
    "jq", "yq", "column", "nl", "seq", "test", "hostname", "ps", "top", "free",
    "man", "help", "history",
})

_TEST_COMMANDS = frozenset({"pytest", "py.test", "tox", "nose2", "jest", "vitest", "mocha"})

_BUILD_COMMANDS = frozenset({
    "tsc", "webpack", "rollup", "vite", "esbuild", "gradle", "mvn", "cmake",
    "gcc", "g++", "clang", "javac", "rustc", "swiftc", "dotnet",
})

_MUTATING_COMMANDS = frozenset({
    "mkdir", "touch", "mv", "cp", "ln", "chmod", "rmdir", "truncate", "tee",
    "patch", "unzip", "tar", "gzip", "gunzip", "zip", "install",
})

_NETWORK_COMMANDS = frozenset({
    "curl", "wget", "nc", "netcat", "ncat", "telnet", "ssh", "scp", "sftp",
    "rsync", "ftp", "http", "httpie", "aria2c",
})

_PRIVILEGED_COMMANDS = frozenset({"sudo", "su", "doas", "pkexec", "runas"})

_DESTRUCTIVE_COMMANDS = frozenset({
    "mkfs", "fdisk", "parted", "shutdown", "reboot", "halt", "poweroff",
    "init", "kill", "killall", "pkill", "userdel", "groupdel",
})

#: `argv[0] → {subcommand → class}`, with `""` as the default for that binary.
_SUBCOMMANDS: Dict[str, Dict[str, str]] = {
    "git": {
        "status": READ_ONLY, "diff": READ_ONLY, "log": READ_ONLY, "show": READ_ONLY,
        "blame": READ_ONLY, "branch": READ_ONLY, "rev-parse": READ_ONLY,
        "ls-files": READ_ONLY, "describe": READ_ONLY, "config": READ_ONLY,
        "add": GIT_MUTATION, "commit": GIT_MUTATION, "checkout": GIT_MUTATION,
        "switch": GIT_MUTATION, "restore": GIT_MUTATION, "stash": GIT_MUTATION,
        "merge": GIT_MUTATION, "rebase": GIT_MUTATION, "cherry-pick": GIT_MUTATION,
        "revert": GIT_MUTATION, "apply": GIT_MUTATION, "am": GIT_MUTATION,
        "tag": GIT_MUTATION, "mv": GIT_MUTATION, "rm": GIT_MUTATION,
        "init": GIT_MUTATION, "gc": GIT_MUTATION, "prune": GIT_MUTATION,
        "reset": GIT_MUTATION, "clean": GIT_MUTATION,
        "push": REMOTE_MUTATION, "fetch": NETWORK, "pull": REMOTE_MUTATION,
        "clone": NETWORK, "remote": READ_ONLY, "submodule": NETWORK,
        "": GIT_MUTATION,
    },
    "npm": {
        "test": TEST, "run": BUILD, "run-script": BUILD, "ci": MUTATING,
        "install": MUTATING, "i": MUTATING, "add": MUTATING, "uninstall": MUTATING,
        "ls": READ_ONLY, "list": READ_ONLY, "view": NETWORK, "audit": READ_ONLY,
        "publish": REMOTE_MUTATION, "version": MUTATING, "link": MUTATING,
        "": MUTATING,
    },
    "yarn": {
        "test": TEST, "run": BUILD, "build": BUILD, "install": MUTATING,
        "add": MUTATING, "remove": MUTATING, "publish": REMOTE_MUTATION,
        "": MUTATING,
    },
    "pnpm": {
        "test": TEST, "run": BUILD, "build": BUILD, "install": MUTATING,
        "add": MUTATING, "remove": MUTATING, "publish": REMOTE_MUTATION,
        "": MUTATING,
    },
    "pip": {
        "install": MUTATING, "uninstall": MUTATING, "download": NETWORK,
        "list": READ_ONLY, "show": READ_ONLY, "freeze": READ_ONLY,
        "": MUTATING,
    },
    "pip3": {
        "install": MUTATING, "uninstall": MUTATING, "download": NETWORK,
        "list": READ_ONLY, "show": READ_ONLY, "freeze": READ_ONLY, "": MUTATING,
    },
    "uv": {
        "run": BUILD, "sync": MUTATING, "pip": MUTATING, "add": MUTATING,
        "remove": MUTATING, "lock": MUTATING, "tree": READ_ONLY,
        "publish": REMOTE_MUTATION, "": MUTATING,
    },
    "poetry": {
        "install": MUTATING, "add": MUTATING, "remove": MUTATING, "run": BUILD,
        "show": READ_ONLY, "publish": REMOTE_MUTATION, "": MUTATING,
    },
    "cargo": {
        "test": TEST, "build": BUILD, "check": BUILD, "clippy": BUILD,
        "fmt": MUTATING, "run": BUILD, "add": MUTATING, "remove": MUTATING,
        "install": MUTATING, "tree": READ_ONLY, "publish": REMOTE_MUTATION,
        "": MUTATING,
    },
    "go": {
        "test": TEST, "build": BUILD, "vet": BUILD, "run": BUILD,
        "list": READ_ONLY, "mod": MUTATING, "get": NETWORK, "install": MUTATING,
        "": BUILD,
    },
    "gh": {
        "pr": REMOTE_MUTATION, "issue": REMOTE_MUTATION, "release": REMOTE_MUTATION,
        "repo": REMOTE_MUTATION, "api": NETWORK, "auth": NETWORK,
        "run": READ_ONLY, "workflow": REMOTE_MUTATION, "": REMOTE_MUTATION,
    },
    # §7.2 deferred the `docker.*` tool namespace, not the word "docker".
    "docker": {
        "ps": READ_ONLY, "images": READ_ONLY, "logs": READ_ONLY,
        "inspect": READ_ONLY, "version": READ_ONLY, "info": READ_ONLY,
        "build": BUILD, "pull": NETWORK, "push": REMOTE_MUTATION,
        "run": PRIVILEGED, "exec": PRIVILEGED, "compose": PRIVILEGED,
        "rm": MUTATING, "rmi": MUTATING, "stop": MUTATING, "kill": MUTATING,
        "system": DESTRUCTIVE, "volume": DESTRUCTIVE, "prune": DESTRUCTIVE,
        "": PRIVILEGED,
    },
    "kubectl": {
        "get": READ_ONLY, "describe": READ_ONLY, "logs": READ_ONLY,
        "apply": PRIVILEGED, "delete": DESTRUCTIVE, "exec": PRIVILEGED,
        "": PRIVILEGED,
    },
    "systemctl": {
        "status": READ_ONLY, "show": READ_ONLY, "list-units": READ_ONLY,
        "": PRIVILEGED,
    },
    "apt": {"": PRIVILEGED},
    "apt-get": {"": PRIVILEGED},
    "yum": {"": PRIVILEGED},
    "dnf": {"": PRIVILEGED},
    "brew": {
        "list": READ_ONLY, "info": READ_ONLY, "install": MUTATING,
        "uninstall": MUTATING, "update": NETWORK, "upgrade": MUTATING, "": MUTATING,
    },
    "make": {
        "test": TEST, "check": TEST, "lint": TEST, "typecheck": TEST,
        "coverage": TEST, "build": BUILD, "": BUILD,
    },
    "just": {"test": TEST, "build": BUILD, "": BUILD},
}

#: Interpreters: `python -m pytest` is a test run, `python script.py` is not.
_MODULE_RUNNERS = {"python", "python3", "py", "node", "deno", "bun", "ruby", "perl", "php"}
_MODULE_CLASSES = {
    "pytest": TEST, "unittest": TEST, "tox": TEST, "nose2": TEST,
    "pip": MUTATING, "build": BUILD, "venv": MUTATING, "http.server": NETWORK,
    "twine": REMOTE_MUTATION,
}

#: Flags that turn an otherwise-harmless command into a mutation.
_MUTATING_FLAGS: Dict[str, Tuple[str, ...]] = {
    "sed": ("-i", "--in-place"),
    "perl": ("-i",),
    "sort": ("-o",),
}

#: `rm` reaching outside the workspace, or recursing from the root, however it is
#: spelled.  Checked on the parsed argv, so quoting and flag order do not hide it.
_ROOT_LIKE = re.compile(r"^(?:/|/\*|~|~/|\$HOME|/\*\*)$")


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------

_SEPARATORS = re.compile(r"&&|\|\||;|\||\n")


def split_segments(command: str) -> List[str]:
    """Split a command line into the pieces that each run something.

    Separators inside quotes are left alone — ``echo "a && b"`` is one segment —
    because a chain that only looks like a chain should not be judged as one.
    """
    if not command:
        return []

    segments: List[str] = []
    current: List[str] = []
    quote: Optional[str] = None
    index = 0

    while index < len(command):
        char = command[index]

        if quote is not None:
            current.append(char)
            if char == quote and (index == 0 or command[index - 1] != "\\"):
                quote = None
            index += 1
            continue

        if char in "\"'":
            quote = char
            current.append(char)
            index += 1
            continue

        match = _SEPARATORS.match(command, index)
        if match:
            segments.append("".join(current))
            current = []
            index = match.end()
            continue

        current.append(char)
        index += 1

    segments.append("".join(current))
    return [segment.strip() for segment in segments if segment.strip()]


def _argv(segment: str) -> List[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        # Unbalanced quotes: fall back to whitespace so an unparseable command is
        # still classified rather than waved through.
        return segment.split()


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_segment(segment: str) -> str:
    """The class of one pipeline segment."""
    argv = _argv(segment)
    if not argv:
        return READ_ONLY

    # Leading VAR=value assignments are not the command.
    while argv and "=" in argv[0] and not argv[0].startswith("-") and "/" not in argv[0].split("=")[0]:
        argv = argv[1:]
    if not argv:
        return MUTATING   # `FOO=bar` alone changes the shell's state

    binary = _basename(argv[0])
    rest = argv[1:]

    if binary in _PRIVILEGED_COMMANDS:
        return PRIVILEGED

    if binary == "rm":
        return DESTRUCTIVE if _rm_is_destructive(rest) else MUTATING

    if binary == "dd":
        return DESTRUCTIVE if any(arg.startswith("of=") for arg in rest) else MUTATING

    if binary in _DESTRUCTIVE_COMMANDS:
        return DESTRUCTIVE

    if binary in _SUBCOMMANDS:
        table = _SUBCOMMANDS[binary]
        subcommand = next((arg for arg in rest if not arg.startswith("-")), "")
        return table.get(subcommand, table.get("", MUTATING))

    if binary in _MODULE_RUNNERS:
        return _classify_interpreter(binary, rest)

    flags = _MUTATING_FLAGS.get(binary)
    if flags and any(arg in flags or arg.startswith(flags[0]) for arg in rest):
        return MUTATING

    if binary in _NETWORK_COMMANDS:
        return NETWORK
    if binary in _TEST_COMMANDS:
        return TEST
    if binary in _BUILD_COMMANDS:
        return BUILD
    if binary in _MUTATING_COMMANDS:
        return MUTATING
    if binary in _READ_ONLY_COMMANDS:
        return READ_ONLY

    # Unknown binary: ask.  Anything else would mean a command we have never
    # heard of is treated as safe because we have never heard of it.
    return MUTATING


def _classify_interpreter(binary: str, rest: Sequence[str]) -> str:
    for index, arg in enumerate(rest):
        if arg == "-m" and index + 1 < len(rest):
            module = rest[index + 1]
            return _MODULE_CLASSES.get(module, _MODULE_CLASSES.get(module.split(".")[0], MUTATING))
        if arg in ("-c", "-e"):
            # An inline program can do anything; it is not readable from here.
            return MUTATING
    if binary in ("node", "deno", "bun") and rest and rest[0] in ("test", "--test"):
        return TEST
    return MUTATING


def _rm_is_destructive(rest: Sequence[str]) -> bool:
    """Whether an ``rm`` is the kind that cannot be undone.

    Recursive-and-forced is treated as destructive wherever it points, and any
    ``rm`` aimed at a root-like path is destructive regardless of flags.
    Grouped short flags (``-rf``, ``-fr``, ``-rfv``) count, which is what the
    substring denylist could not see.
    """
    recursive = force = False
    targets: List[str] = []
    for arg in rest:
        if arg.startswith("--"):
            if arg == "--recursive":
                recursive = True
            elif arg == "--force":
                force = True
            elif arg == "--no-preserve-root":
                return True
        elif arg.startswith("-") and len(arg) > 1:
            letters = set(arg[1:])
            recursive = recursive or bool(letters & {"r", "R"})
            force = force or "f" in letters
        else:
            targets.append(arg)

    if any(_ROOT_LIKE.match(target) for target in targets):
        return True
    return recursive and force


def _basename(command: str) -> str:
    return command.rsplit("/", 1)[-1]


def classify(command: str) -> str:
    """The class of a whole command line — its worst segment."""
    if blocked_reason(command):
        # The 0C denylist is a floor, not the whole answer.
        return DESTRUCTIVE
    segments = split_segments(command)
    if not segments:
        return READ_ONLY
    return max((classify_segment(s) for s in segments), key=lambda c: _SEVERITY[c])


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

#: Class → (verdict, risk) in `normal` mode, per §8.3's table.
_VERDICTS: Dict[str, Tuple[str, Risk]] = {
    READ_ONLY: ("allow", Risk.SAFE),
    TEST: ("allow", Risk.SAFE),
    BUILD: ("allow", Risk.SAFE),
    MUTATING: ("ask", Risk.APPROVAL),
    GIT_MUTATION: ("ask", Risk.APPROVAL),
    REMOTE_MUTATION: ("ask", Risk.HIGH_RISK),
    NETWORK: ("ask", Risk.HIGH_RISK),
    DESTRUCTIVE: ("deny", Risk.HIGH_RISK),
    PRIVILEGED: ("deny", Risk.HIGH_RISK),
}


def classify_command(command: str, *, allow_network: bool = True) -> CommandVerdict:
    """Classify *command* and say what should happen to it.

    ``NETWORK`` is denied outright when the sandbox forbids the network: asking
    a user to approve something the sandbox will then refuse is a prompt with no
    outcome. That is consistency with the enforcement layer, not an extra rule.
    """
    command_class = classify(command)
    verdict, risk = _VERDICTS[command_class]
    reason = f"this command {DESCRIPTIONS[command_class]} ({command_class})"

    if command_class == NETWORK and not allow_network:
        return CommandVerdict(
            command_class=command_class, verdict="deny",
            reason="the sandbox has network access disabled, so this command cannot succeed",
            risk=risk.value,
        )

    if command_class == DESTRUCTIVE:
        matched = blocked_reason(command)
        if matched:
            reason = f"this command matches the blocked pattern {matched!r} (DESTRUCTIVE)"

    return CommandVerdict(
        command_class=command_class, verdict=verdict, reason=reason, risk=risk.value,
    )


def describe(command: str) -> str:
    """One human sentence for an approval card."""
    command_class = classify(command)
    return f"This command {DESCRIPTIONS[command_class]} ({command_class})."
