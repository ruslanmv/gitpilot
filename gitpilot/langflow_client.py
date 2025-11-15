from __future__ import annotations

from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException

from .settings import get_settings


async def run_langflow_flow(
    flow_id: str,
    input_value: str,
    *,
    session_id: str = "gitpilot-session",
    tweaks: Optional[Dict[str, Any]] = None,
) -> str:
    """Run a LangFlow flow and return the first chat-like output as text."""
    settings = get_settings()
    url = f"{settings.langflow_url.rstrip('/')}/api/v1/run/{flow_id}"
    headers = {"Content-Type": "application/json"}
    if settings.langflow_api_key:
        headers["x-api-key"] = settings.langflow_api_key

    payload: Dict[str, Any] = {
        "input_value": input_value,
        "session_id": session_id,
        "input_type": "chat",
        "output_type": "chat",
        "output_component": "",
        "tweaks": tweaks or {},
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, json=payload)

    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, resp.text)

    data = resp.json()
    try:
        outputs = data["outputs"][0]["outputs"][0]["results"]
        if isinstance(outputs, dict):
            for key in ("message", "text", "output_text"):
                if key in outputs:
                    return str(outputs[key])
    except Exception:
        pass

    return str(data)
