"""Semantic command classification — Batch V4-D2.

Table-driven, because the value of a classifier is in its coverage rather than in
any one case, and a table is where a missing row is visible.
"""
from __future__ import annotations

import pytest

from gitpilot.agent.command_class import (
    BUILD,
    DESTRUCTIVE,
    GIT_MUTATION,
    MUTATING,
    NETWORK,
    PRIVILEGED,
    READ_ONLY,
    REMOTE_MUTATION,
    TEST,
    classify,
    classify_command,
    classify_segment,
    describe,
    split_segments,
)

# ---------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------

CORPUS = [
    # read-only
    ("ls -la", READ_ONLY),
    ("cat pyproject.toml", READ_ONLY),
    ("grep -rn foo src/", READ_ONLY),
    ("find . -name '*.py'", READ_ONLY),
    ("which python", READ_ONLY),
    ("git status", READ_ONLY),
    ("git diff HEAD~1", READ_ONLY),
    ("git log --oneline -5", READ_ONLY),
    ("/usr/bin/ls", READ_ONLY),
    ("jq '.name' package.json", READ_ONLY),
    # tests
    ("pytest -q", TEST),
    ("pytest tests/test_a.py::test_b", TEST),
    ("make test", TEST),
    ("npm test", TEST),
    ("cargo test", TEST),
    ("go test ./...", TEST),
    ("python -m pytest", TEST),
    ("uv run --extra dev pytest", BUILD),
    ("tox", TEST),
    # builds
    ("npm run build", BUILD),
    ("tsc --noEmit", BUILD),
    ("cargo build --release", BUILD),
    ("make", BUILD),
    ("go build ./cmd/app", BUILD),
    # mutations
    ("pip install requests", MUTATING),
    ("npm install", MUTATING),
    ("cargo add serde", MUTATING),
    ("mkdir -p build", MUTATING),
    ("mv a.py b.py", MUTATING),
    ("cp -r src dst", MUTATING),
    ("chmod +x run.sh", MUTATING),
    ("sed -i 's/a/b/' f.py", MUTATING),
    ("sed --in-place 's/a/b/' f.py", MUTATING),
    ("touch newfile", MUTATING),
    ("rm build/artifact.o", MUTATING),
    # local git
    ("git add -A", GIT_MUTATION),
    ("git commit -m 'x'", GIT_MUTATION),
    ("git checkout -b feature", GIT_MUTATION),
    ("git stash", GIT_MUTATION),
    ("git reset --hard HEAD~1", GIT_MUTATION),
    ("git clean -fd", GIT_MUTATION),
    # remote
    ("git push origin main", REMOTE_MUTATION),
    ("git push --force", REMOTE_MUTATION),
    ("gh pr create --title x", REMOTE_MUTATION),
    ("npm publish", REMOTE_MUTATION),
    ("git pull", REMOTE_MUTATION),
    # network
    ("curl https://example.com", NETWORK),
    ("wget https://example.com/f.tar.gz", NETWORK),
    ("pip download requests", NETWORK),
    ("git clone https://github.com/a/b", NETWORK),
    ("ssh host", NETWORK),
    # destructive
    ("rm -rf /", DESTRUCTIVE),
    ("rm -rf build", DESTRUCTIVE),
    ("mkfs.ext4 /dev/sda1", DESTRUCTIVE),
    ("dd of=/dev/sda if=/dev/zero", DESTRUCTIVE),
    ("shutdown -h now", DESTRUCTIVE),
    ("kubectl delete pod x", DESTRUCTIVE),
    ("docker system prune -af", DESTRUCTIVE),
    # privileged
    ("sudo pytest", PRIVILEGED),
    ("su - root", PRIVILEGED),
    ("apt-get install curl", PRIVILEGED),
    ("systemctl restart nginx", PRIVILEGED),
    ("docker run -v /:/host alpine", PRIVILEGED),
]


class TestCorpus:
    @pytest.mark.parametrize(("command", "expected"), CORPUS)
    def test_each_command_lands_in_its_class(self, command, expected):
        assert classify(command) == expected, command


# ---------------------------------------------------------------------------
# Chains and pipes
# ---------------------------------------------------------------------------


class TestSegmentation:
    def test_a_chain_takes_its_worst_segment(self):
        assert classify("pytest && git push origin main") == REMOTE_MUTATION

    def test_a_pipe_is_segmented(self):
        assert classify("cat f.txt | grep foo | wc -l") == READ_ONLY

    def test_sudo_anywhere_in_a_chain_is_privileged(self):
        """Judging only the first command is how `ls && sudo rm -rf /` gets
        classified as a directory listing."""
        assert classify("ls -la && sudo apt-get install foo") == PRIVILEGED
        assert classify("echo hi; sudo reboot") == PRIVILEGED
        assert classify("make | sudo tee /etc/x") == PRIVILEGED

    def test_a_destructive_tail_is_caught(self):
        assert classify("pytest -q ; rm -rf /") == DESTRUCTIVE

    def test_separators_inside_quotes_are_not_separators(self):
        assert split_segments('echo "a && b"') == ['echo "a && b"']
        assert classify('echo "a && b"') == READ_ONLY

    def test_the_denylist_floor_is_kept_even_where_it_overreaches(self):
        """`echo "rm -rf /"` is harmless, and is still refused.

        The 0C substring denylist matches anywhere in the line, and
        `SandboxPolicy.validate` refuses on the same rule — so the sandbox would
        decline to run this regardless of what the classifier said. Classifying
        it as safe would produce an approval prompt for something that then
        fails, which is worse than a conservative refusal. Consistency with the
        enforcement layer beats precision here.
        """
        assert classify('echo "rm -rf /"') == DESTRUCTIVE

    def test_newlines_separate_segments(self):
        assert classify("pytest\ngit push") == REMOTE_MUTATION

    def test_or_chains_are_segmented(self):
        assert classify("pytest || npm publish") == REMOTE_MUTATION

    @pytest.mark.parametrize("command", ["", "   ", "\n"])
    def test_nothing_to_run_is_read_only(self, command):
        assert classify(command) == READ_ONLY

    def test_unbalanced_quotes_still_classify(self):
        """An unparseable command must not fall through as safe."""
        assert classify("sudo 'unterminated") == PRIVILEGED


# ---------------------------------------------------------------------------
# rm, spelled every way
# ---------------------------------------------------------------------------


class TestObfuscatedRm:
    @pytest.mark.parametrize("command", [
        "rm -rf /",
        "rm -fr /",
        "rm -rfv /",
        "rm -r -f /",
        "rm --recursive --force /",
        "rm --no-preserve-root -r /",
        "rm -rf ~",
        "rm -rf $HOME",
        "rm -rf /*",
        "/bin/rm -rf /",
    ])
    def test_each_spelling_is_destructive(self, command):
        """The substring denylist caught `rm -rf /` and nothing else — grouped
        flags, long options and reordering all walked straight past it."""
        assert classify(command) == DESTRUCTIVE, command

    @pytest.mark.parametrize("command", ["rm a.py", "rm -f a.py", "rm -r build"])
    def test_an_ordinary_removal_is_only_a_mutation(self, command):
        assert classify(command) == MUTATING, command


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


class TestVerdicts:
    @pytest.mark.parametrize(("command", "verdict"), [
        ("ls", "allow"),
        ("pytest", "allow"),
        ("make build", "allow"),
        ("pip install x", "ask"),
        ("git commit -m x", "ask"),
        ("git push", "ask"),
        ("rm -rf /", "deny"),
        ("sudo ls", "deny"),
    ])
    def test_the_default_verdict_per_class(self, command, verdict):
        assert classify_command(command).verdict == verdict

    def test_destructive_and_privileged_never_reach_a_prompt(self):
        for command in ("rm -rf /", "mkfs /dev/sda", "sudo rm x", "shutdown now"):
            assert classify_command(command).verdict == "deny", command

    def test_network_is_denied_when_the_sandbox_forbids_it(self):
        """Asking a user to approve something the sandbox will then refuse is a
        prompt with no outcome."""
        assert classify_command("curl https://x", allow_network=True).verdict == "ask"

        denied = classify_command("curl https://x", allow_network=False)
        assert denied.verdict == "deny"
        assert "network access disabled" in denied.reason

    def test_a_denylisted_pattern_names_the_pattern(self):
        verdict = classify_command("mkfs.ext4 /dev/sda1")
        assert verdict.verdict == "deny"
        assert "mkfs" in verdict.reason

    def test_the_prompt_text_names_the_class(self):
        """"Run a shell command?" tells the user nothing they did not know."""
        text = describe("pip install requests")
        assert "MUTATING" in text
        assert "packages" in text

        assert "REMOTE_MUTATION" in describe("git push")
        assert "publishes" in describe("git push")

    def test_every_class_has_a_description(self):
        for command, expected in CORPUS:
            verdict = classify_command(command, allow_network=True)
            assert verdict.description
            assert "unrecognised" not in verdict.description, expected


# ---------------------------------------------------------------------------
# Unknown commands
# ---------------------------------------------------------------------------


class TestUnknown:
    @pytest.mark.parametrize("command", [
        "wibble", "frobnicate --all", "./scripts/deploy.sh", "my-custom-tool run",
    ])
    def test_an_unrecognised_binary_asks(self, command):
        """Anything else means a command we have never heard of is treated as
        safe because we have never heard of it."""
        verdict = classify_command(command)
        assert verdict.command_class == MUTATING
        assert verdict.verdict == "ask"

    def test_an_inline_program_is_not_read(self):
        assert classify('python -c "import shutil; shutil.rmtree(\'/\')"') == MUTATING

    def test_leading_env_assignments_are_skipped(self):
        assert classify("FOO=bar BAZ=qux pytest") == TEST

    def test_a_bare_assignment_is_a_mutation(self):
        assert classify_segment("FOO=bar") == MUTATING
