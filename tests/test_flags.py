"""Tests for the feature-flag service."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import pytest

from gitpilot import flags


@pytest.fixture(autouse=True)
def _isolated_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Reset module state for each test and redirect the user flags path."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(flags, "USER_FLAGS_PATH", home / ".gitpilot" / "flags.json")
    flags.set_workspace(None)
    flags.clear_all_overrides()
    monkeypatch.delenv(flags.ENV_VAR, raising=False)
    flags.reload()
    yield tmp_path
    flags.clear_all_overrides()
    flags.set_workspace(None)


def test_unknown_flag_returns_default() -> None:
    assert flags.is_on("nothing-defined") is False
    assert flags.is_on("nothing-defined", default=True) is True


def test_env_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(flags.ENV_VAR, "alpha=1,beta=0")
    flags.reload()
    assert flags.is_on("alpha") is True
    assert flags.is_on("beta", default=True) is False


def test_env_parses_truthy_synonyms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(flags.ENV_VAR, "a=true,b=YES,c=on,d=false,e=NO,f=off")
    flags.reload()
    assert flags.is_on("a") is True
    assert flags.is_on("b") is True
    assert flags.is_on("c") is True
    assert flags.is_on("d") is False
    assert flags.is_on("e") is False
    assert flags.is_on("f") is False


def test_env_bare_name_is_truthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(flags.ENV_VAR, "solo, ignored=,, alpha=1")
    flags.reload()
    assert flags.is_on("solo") is True
    assert flags.is_on("alpha") is True
    assert flags.is_on("ignored") is False  # no value → discarded


def test_project_file_loads(_isolated_state: Path) -> None:
    ws = _isolated_state / "ws"
    (ws / ".gitpilot").mkdir(parents=True)
    (ws / ".gitpilot" / "flags.json").write_text(json.dumps({"prompt_cache": True}))
    flags.set_workspace(ws)
    assert flags.is_on("prompt_cache") is True


def test_user_file_loads(_isolated_state: Path) -> None:
    flags.USER_FLAGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    flags.USER_FLAGS_PATH.write_text(json.dumps({"stream_v2": True}))
    flags.reload()
    assert flags.is_on("stream_v2") is True


def test_precedence_override_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(flags.ENV_VAR, "alpha=0")
    flags.reload()
    flags.set_override("alpha", True)
    assert flags.is_on("alpha") is True


def test_precedence_env_beats_project(_isolated_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _isolated_state / "ws"
    (ws / ".gitpilot").mkdir(parents=True)
    (ws / ".gitpilot" / "flags.json").write_text(json.dumps({"alpha": False}))
    monkeypatch.setenv(flags.ENV_VAR, "alpha=1")
    flags.set_workspace(ws)
    assert flags.is_on("alpha") is True


def test_precedence_project_beats_user(_isolated_state: Path) -> None:
    ws = _isolated_state / "ws"
    (ws / ".gitpilot").mkdir(parents=True)
    (ws / ".gitpilot" / "flags.json").write_text(json.dumps({"alpha": True}))
    flags.USER_FLAGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    flags.USER_FLAGS_PATH.write_text(json.dumps({"alpha": False}))
    flags.set_workspace(ws)
    assert flags.is_on("alpha") is True


def test_clear_override_restores_lower_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(flags.ENV_VAR, "alpha=1")
    flags.reload()
    flags.set_override("alpha", False)
    assert flags.is_on("alpha") is False
    flags.clear_override("alpha")
    assert flags.is_on("alpha") is True


def test_enabled_flags_snapshot_is_a_copy() -> None:
    flags.set_override("foo", True)
    snap = flags.enabled_flags()
    snap["foo"] = False
    assert flags.is_on("foo") is True


def test_invalid_json_file_falls_back_to_lower_sources(
    _isolated_state: Path, caplog: pytest.LogCaptureFixture
) -> None:
    flags.USER_FLAGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    flags.USER_FLAGS_PATH.write_text("{not json")
    flags.reload()
    assert flags.is_on("anything") is False  # default still wins


def test_non_boolean_values_in_json_are_ignored(_isolated_state: Path) -> None:
    flags.USER_FLAGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    flags.USER_FLAGS_PATH.write_text(json.dumps({"alpha": "definitely", "beta": 1}))
    flags.reload()
    assert flags.is_on("alpha") is False  # unparseable → discarded
    assert flags.is_on("beta") is True   # numeric truthy


def test_reload_picks_up_file_changes(_isolated_state: Path) -> None:
    flags.USER_FLAGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    flags.USER_FLAGS_PATH.write_text(json.dumps({"alpha": False}))
    flags.reload()
    assert flags.is_on("alpha") is False
    flags.USER_FLAGS_PATH.write_text(json.dumps({"alpha": True}))
    flags.reload()
    assert flags.is_on("alpha") is True


def test_iter_known_pairs_known_defaults() -> None:
    flags.set_override("present", True)
    seen = {name: (current, default) for name, current, default in
            flags.iter_known({"present": False, "absent": True})}
    assert seen["present"] == (True, False)
    assert seen["absent"] == (True, True)


def test_thread_safety_under_concurrent_access(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    monkeypatch.setenv(flags.ENV_VAR, "alpha=1,beta=0")
    flags.reload()
    errors: list[Exception] = []

    def worker() -> None:
        try:
            for i in range(200):
                flags.is_on("alpha")
                flags.set_override(f"runtime-{i % 5}", bool(i % 2))
                flags.enabled_flags()
        except Exception as exc:  # pragma: no cover - failure signal
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
