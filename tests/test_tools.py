"""The fourteen tools, driven through a real MCP client with the wishlist API stubbed.

What these check is this server's own job: shaping requests, shaping responses,
and turning API errors into sentences a model can act on. The visibility and
rate-limit rules belong to the wishlist API and are tested there; here we only
prove we ask it the right question and repeat its answer faithfully.
"""

from unittest.mock import patch

import httpx
import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError

from tests.conftest import API, make_token
from wishlist_mcp.server import build_server


@pytest.fixture
def call(bearer):
    """Invoke a tool with a valid bearer token in scope."""

    async def _call(name, arguments=None):
        with patch(
            "wishlist_mcp.tools.get_http_headers",
            return_value={"authorization": bearer},
        ):
            async with Client(build_server()) as client:
                result = await client.call_tool(name, arguments or {})
                return _plain(result.structured_content)

    return _call


def _plain(content):
    """A tool returning a list is wrapped as {"result": [...]}, because JSON
    Schema cannot describe a bare array as an object."""
    if isinstance(content, dict) and set(content) == {"result"}:
        return content["result"]
    return content


def ok(data):
    return httpx.Response(200, json={"data": data})


def err(status, code, message):
    return httpx.Response(status, json={"error": {"code": code, "message": message}})


# --- the surface -------------------------------------------------------------


async def test_every_tool_in_the_contract_is_present():
    async with Client(build_server()) as client:
        names = {t.name for t in await client.list_tools()}

    assert names == {
        "wishlist_search_people",
        "wishlist_get_gift_guide",
        "wishlist_get_profile",
        "wishlist_get_wishlist",
        "wishlist_get_my_profile",
        "wishlist_get_my_wishlist",
        "wishlist_list_circle",
        "wishlist_preview_invite",
        "wishlist_add_item",
        "wishlist_update_item",
        "wishlist_delete_item",
        "wishlist_update_my_profile",
        "wishlist_create_invite",
        "wishlist_accept_invite",
    }


async def test_the_two_irreversible_tools_are_marked_destructive():
    """ChatGPT confirms destructive actions by default and Claude does in most
    surfaces, so this annotation is the cheapest guardrail on the write set."""
    async with Client(build_server()) as client:
        tools = {t.name: t for t in await client.list_tools()}

    assert tools["wishlist_create_invite"].annotations.destructiveHint is True
    assert tools["wishlist_delete_item"].annotations.destructiveHint is True
    assert tools["wishlist_add_item"].annotations.destructiveHint is False


# --- the token is forwarded --------------------------------------------------


@respx.mock
async def test_the_callers_token_is_forwarded_to_the_api(call, bearer):
    """The whole authorization model. The API applies the rules to whoever the
    token names, so a call with nobody's token must not become a call with
    everybody's."""
    route = respx.get(f"{API}/api/v1/profiles/me").mock(
        return_value=ok({"username": "alice", "visibility": {}})
    )

    await call("wishlist_get_my_profile")

    assert route.calls.last.request.headers["authorization"] == bearer


@respx.mock
async def test_a_token_for_another_resource_never_reaches_the_api(keypair):
    """The audience check has to happen here. Only this server knows what its
    own resource URI is."""
    route = respx.get(f"{API}/api/v1/profiles/me").mock(return_value=ok({}))
    wrong = f"Bearer {make_token(keypair, aud='https://elsewhere.example.com/mcp')}"

    with patch(
        "wishlist_mcp.tools.get_http_headers", return_value={"authorization": wrong}
    ):
        async with Client(build_server()) as client:
            with pytest.raises(ToolError) as excinfo:
                await client.call_tool("wishlist_get_my_profile", {})

    assert "different resource" in str(excinfo.value)
    assert not route.called, "a refused token must not produce an upstream call"


# --- reads -------------------------------------------------------------------


@respx.mock
async def test_the_gift_guide_combines_profile_and_wishlist(call):
    respx.get(f"{API}/api/v1/profiles/sarah").mock(
        return_value=ok(
            {
                "username": "sarah",
                "shirt_size": "M",
                "allergies": None,
                "hidden_from_you": ["allergies"],
                "in_your_circle": False,
                "id": 1,
                "user_id": 1,
            }
        )
    )
    respx.get(f"{API}/api/v1/wishlists/sarah").mock(
        return_value=ok({"items": [{"id": 3, "name": "headphones", "priority": "high"}]})
    )

    guide = await call("wishlist_get_gift_guide", {"username": "sarah"})

    assert guide["profile"]["shirt_size"] == "M"
    assert guide["profile"]["hidden_from_you"] == ["allergies"]
    assert guide["in_your_circle"] is False
    assert [i["name"] for i in guide["items"]] == ["headphones"]
    assert "id" not in guide["profile"], "database keys mean nothing to a model"


@respx.mock
async def test_in_your_circle_comes_from_the_api_not_a_guess(call):
    """An empty hidden_from_you is ambiguous, so this must not be inferred."""
    respx.get(f"{API}/api/v1/profiles/sarah").mock(
        return_value=ok(
            {"username": "sarah", "hidden_from_you": [], "in_your_circle": True}
        )
    )
    respx.get(f"{API}/api/v1/wishlists/sarah").mock(return_value=ok({"items": []}))

    guide = await call("wishlist_get_gift_guide", {"username": "sarah"})

    assert guide["in_your_circle"] is True


@respx.mock
async def test_search_caps_at_twenty(call):
    respx.get(f"{API}/api/v1/search/people").mock(
        return_value=ok(
            {"results": [{"username": f"u{i}", "display_name": None} for i in range(20)]}
        )
    )

    assert len(await call("wishlist_search_people", {"query": "u"})) == 20
    assert len(await call("wishlist_search_people", {"query": "u", "limit": 5})) == 5


async def test_an_empty_search_does_not_call_the_api(call):
    with respx.mock:
        route = respx.get(f"{API}/api/v1/search/people").mock(return_value=ok({}))
        assert await call("wishlist_search_people", {"query": "   "}) == []
        assert not route.called


# --- writes ------------------------------------------------------------------


@respx.mock
async def test_only_the_fields_you_set_are_sent(call):
    """Sending an explicit null would clear a field the user never mentioned,
    which is how an agent quietly wipes someone's address."""
    route = respx.patch(f"{API}/api/v1/wishlists/mine/items/7").mock(
        return_value=ok({"id": 7, "name": "kept", "visibility": "public"})
    )

    await call("wishlist_update_item", {"item_id": 7, "priority": "low"})

    import json

    assert json.loads(route.calls.last.request.content) == {"priority": "low"}


async def test_an_update_with_nothing_to_change_is_refused(call):
    with pytest.raises(ToolError) as excinfo:
        await call("wishlist_update_item", {"item_id": 7})

    assert "at least one field" in str(excinfo.value)


@respx.mock
async def test_adding_an_item_defaults_to_public(call):
    route = respx.post(f"{API}/api/v1/wishlists/mine/items").mock(
        return_value=ok({"id": 9, "name": "thing", "visibility": "public"})
    )

    result = await call("wishlist_add_item", {"name": "thing"})

    import json

    assert json.loads(route.calls.last.request.content)["visibility"] == "public"
    assert result["id"] == 9


@respx.mock
async def test_deleting_reports_the_id_it_removed(call):
    respx.delete(f"{API}/api/v1/wishlists/mine/items/9").mock(
        return_value=httpx.Response(204)
    )

    assert await call("wishlist_delete_item", {"item_id": 9}) == {
        "deleted": True,
        "item_id": 9,
    }


@respx.mock
async def test_accepting_an_invite_reports_who_sent_it(call):
    respx.post(f"{API}/api/v1/invites/tok/accept").mock(
        return_value=ok({"invite": {"status": "accepted", "inviter_username": "sarah"}})
    )

    assert await call("wishlist_accept_invite", {"invite_token": "tok"}) == {
        "accepted": True,
        "inviter_username": "sarah",
    }


# --- errors a model can act on -----------------------------------------------


@respx.mock
async def test_a_rate_limit_reaches_the_model_verbatim(call):
    """A bare error code makes a model retry in a loop, which is the behaviour
    the limit exists to stop."""
    respx.get(f"{API}/api/v1/wishlists/mine").mock(
        return_value=err(
            429,
            "rate_limited",
            "rate limit reached, 60 calls per minute. Try again in 34 seconds.",
        )
    )

    with pytest.raises(ToolError) as excinfo:
        await call("wishlist_get_my_wishlist")

    assert "Try again in 34 seconds" in str(excinfo.value)


@respx.mock
async def test_a_duplicate_invite_reaches_the_model_verbatim(call):
    respx.post(f"{API}/api/v1/invites").mock(
        return_value=err(
            409,
            "invite_already_pending",
            "An invite to that address is already pending, sent 2026-08-20.",
        )
    )

    with pytest.raises(ToolError) as excinfo:
        await call("wishlist_create_invite", {"email": "friend@example.com"})

    assert "already pending" in str(excinfo.value)


@respx.mock
async def test_an_unknown_username_says_what_to_do_next(call):
    respx.get(f"{API}/api/v1/profiles/nobody").mock(
        return_value=err(404, "not_found", "User not found.")
    )

    with pytest.raises(ToolError) as excinfo:
        await call("wishlist_get_profile", {"username": "nobody"})

    message = str(excinfo.value)
    assert "wishlist_search_people" in message
    # The API is inconsistent about trailing punctuation, and the join is what a
    # model reads. "User not found Try wishlist_search_people" is one garbled
    # sentence, not two.
    assert "User not found. Try" in message


@respx.mock
async def test_an_expired_session_tells_the_user_to_reconnect(call):
    respx.get(f"{API}/api/v1/profiles/me").mock(
        return_value=err(401, "unauthorized", "Not authenticated")
    )

    with pytest.raises(ToolError) as excinfo:
        await call("wishlist_get_my_profile")

    assert "Reconnect the wishlist server" in str(excinfo.value)


@respx.mock
async def test_an_unreachable_api_says_try_again_rather_than_leaking_the_error(call):
    respx.get(f"{API}/api/v1/profiles/me").mock(side_effect=httpx.ConnectError("down"))

    with pytest.raises(ToolError) as excinfo:
        await call("wishlist_get_my_profile")

    assert "Try again in a moment" in str(excinfo.value)
    assert "ConnectError" not in str(excinfo.value)
