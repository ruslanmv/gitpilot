from __future__ import annotations

from contextlib import contextmanager

import pytest

from gitpilot import api


@contextmanager
def _noop_execution_context(*_args, **_kwargs):
    yield


@pytest.mark.asyncio
async def test_api_chat_plan_falls_back_to_lite_on_planresult_json_parse_error(monkeypatch):
    req = api.ChatPlanRequest(
        repo_owner="octo",
        repo_name="demo",
        goal="Generate a simple plan",
        branch_name="main",
    )

    async def _raise_parse_error(*_args, **_kwargs):
        raise Exception("ValidationError: 1 validation error for PlanResult: json_invalid")

    async def _lite_success(*_args, **_kwargs):
        return api.PlanResult(goal=req.goal, summary="lite-fallback", steps=[])

    monkeypatch.setattr(api, "get_github_token", lambda _auth: "token")
    monkeypatch.setattr(api, "execution_context", _noop_execution_context)
    monkeypatch.setattr(api, "_is_lite_mode_active", lambda: False)
    monkeypatch.setattr(api, "generate_plan", _raise_parse_error)
    monkeypatch.setattr(api, "generate_plan_lite", _lite_success)

    result = await api.api_chat_plan(req, authorization=None)

    assert isinstance(result, api.PlanResult)
    assert result.summary == "lite-fallback"
