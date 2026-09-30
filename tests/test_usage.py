"""One usage event per tool call, reported to the wishlist API.

The rule these hold is that reporting is a side effect: it says what happened to
a tool call and can never change what happened to it.
"""

import json
from unittest.mock import patch

import httpx
import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError

from tests.conftest import API
from wishlist_mcp.config import settings
from wishlist_mcp.server import build_server

EVENTS = f"{API}/api/v1/events"
PROFILE = {"username": "alice", "visibility": {}}


@pytest.fixture
def call(bearer):
    """Invoke a tool the way a connected client does: the bearer token is on the
    HTTP request, where both the tool and the reporter read it."""

    async def _call(name, arguments=None):
        headers = {"authorization": bearer}
        with (
            patch("wishlist_mcp.tools.get_http_headers", return_value=headers),
            patch("wishlist_mcp.usage.get_http_headers", return_value=headers),
        ):
            async with Client(build_server()) as client:
                return await client.call_tool(name, arguments or {})

    return _call


def ok(data):
    return httpx.Response(200, json={"data": data})


def reported(route) -> list[dict]:
    """The events posted to the API, one per request."""
    sent = []
    for request_call in route.calls:
        (event,) = json.loads(request_call.request.content)["events"]
        sent.append(event)
    return sent


@respx.mock
async def test_a_tool_call_is_reported_once_with_the_callers_token(call, bearer):
    respx.get(f"{API}/api/v1/profiles/me").mock(return_value=ok(PROFILE))
    events = respx.post(EVENTS).mock(return_value=httpx.Response(202))

    await call("wishlist_get_my_profile")

    (event,) = reported(events)
    assert event["name"] == "mcp_tool_call"
    assert event["props"]["tool"] == "wishlist_get_my_profile"
    assert event["props"]["ok"] is True
    assert isinstance(event["props"]["ms"], int)
    assert "error" not in event["props"]
    assert events.calls.last.request.headers["authorization"] == bearer


@respx.mock
async def test_nothing_the_tool_was_given_or_returned_is_reported(call):
    """Arguments and results hold other people's names and wishlists. A usage
    count needs the tool's name and nothing about who it was pointed at."""
    respx.get(f"{API}/api/v1/profiles/sarah").mock(
        return_value=ok({"username": "sarah", "shirt_size": "M"})
    )
    respx.get(f"{API}/api/v1/wishlists/sarah").mock(
        return_value=ok({"items": [{"id": 3, "name": "headphones"}]})
    )
    events = respx.post(EVENTS).mock(return_value=httpx.Response(202))

    await call("wishlist_get_gift_guide", {"username": "sarah"})

    sent = reported(events)
    assert len(sent) == 1, "two API requests are still one tool call"
    assert set(sent[0]["props"]) == {"tool", "ok", "ms"}
    body = events.calls.last.request.content.decode()
    assert "sarah" not in body
    assert "headphones" not in body


@respx.mock
async def test_a_failed_call_reports_the_apis_error_code(call):
    respx.get(f"{API}/api/v1/profiles/me").mock(
        return_value=httpx.Response(
            429, json={"error": {"code": "rate_limited", "message": "Slow down."}}
        )
    )
    events = respx.post(EVENTS).mock(return_value=httpx.Response(202))

    with pytest.raises(ToolError, match="Slow down"):
        await call("wishlist_get_my_profile")

    (event,) = reported(events)
    assert event["props"]["ok"] is False
    assert event["props"]["error"] == "rate_limited"


@respx.mock
async def test_an_unreachable_api_is_reported_as_unreachable(call):
    respx.get(f"{API}/api/v1/profiles/me").mock(side_effect=httpx.ConnectError("down"))
    events = respx.post(EVENTS).mock(return_value=httpx.Response(202))

    with pytest.raises(ToolError):
        await call("wishlist_get_my_profile")

    (event,) = reported(events)
    assert event["props"]["error"] == "unreachable"


@respx.mock
async def test_an_error_without_a_code_reports_its_status(call):
    respx.get(f"{API}/api/v1/profiles/me").mock(return_value=httpx.Response(502))
    events = respx.post(EVENTS).mock(return_value=httpx.Response(202))

    with pytest.raises(ToolError):
        await call("wishlist_get_my_profile")

    (event,) = reported(events)
    assert event["props"]["error"] == "http_502"


@pytest.mark.parametrize(
    "failure",
    [
        {"return_value": httpx.Response(500)},
        {"return_value": httpx.Response(400, json={"error": {"code": "reserved"}})},
        {"side_effect": httpx.ConnectError("down")},
        {"side_effect": httpx.ReadTimeout("slow")},
    ],
)
@respx.mock
async def test_a_failure_to_report_never_fails_the_tool(call, failure):
    respx.get(f"{API}/api/v1/profiles/me").mock(return_value=ok(PROFILE))
    respx.post(EVENTS).mock(**failure)

    result = await call("wishlist_get_my_profile")

    assert result.structured_content["username"] == "alice"


@respx.mock
async def test_reporting_can_be_switched_off(call, monkeypatch):
    monkeypatch.setattr(settings, "report_usage", False)
    respx.get(f"{API}/api/v1/profiles/me").mock(return_value=ok(PROFILE))
    events = respx.post(EVENTS).mock(return_value=httpx.Response(202))

    await call("wishlist_get_my_profile")

    assert not events.called


@respx.mock
async def test_a_call_with_no_token_reports_nothing(bearer):
    """The token is what tells the API whose call it was and that this server
    sent it. Without one there is nothing true to report."""
    respx.get(f"{API}/api/v1/profiles/me").mock(return_value=ok(PROFILE))
    events = respx.post(EVENTS).mock(return_value=httpx.Response(202))

    with patch(
        "wishlist_mcp.tools.get_http_headers", return_value={"authorization": bearer}
    ):
        async with Client(build_server()) as client:
            await client.call_tool("wishlist_get_my_profile", {})

    assert not events.called
