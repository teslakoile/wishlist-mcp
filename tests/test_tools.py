"""The twenty-four tools, driven through a real MCP client with the wishlist API stubbed.

What these check is this server's own job: shaping requests, shaping responses,
and turning API errors into sentences a model can act on. The visibility and
rate-limit rules belong to the wishlist API and are tested there; here we only
prove we ask it the right question and repeat its answer faithfully.
"""

import json
import pathlib
import re
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
        "wishlist_list_circle_requests",
        "wishlist_preview_invite",
        "wishlist_add_item",
        "wishlist_update_item",
        "wishlist_delete_item",
        "wishlist_update_my_profile",
        "wishlist_create_invite",
        "wishlist_accept_invite",
        "wishlist_request_circle",
        "wishlist_accept_circle_request",
        "wishlist_decline_circle_request",
        "wishlist_remove_circle_member",
        "wishlist_upcoming_occasions",
        "wishlist_list_notifications",
        "wishlist_get_reminder_preferences",
        "wishlist_update_reminder_preferences",
        "wishlist_mark_notifications_read",
    }


async def test_the_irreversible_tools_are_marked_destructive():
    """ChatGPT confirms destructive actions by default and Claude does in most
    surfaces, so this annotation is the cheapest guardrail on the write set."""
    async with Client(build_server()) as client:
        tools = {t.name: t for t in await client.list_tools()}

    for name in (
        "wishlist_create_invite",
        "wishlist_delete_item",
        "wishlist_remove_circle_member",
    ):
        assert tools[name].annotations.destructiveHint is True, name
    assert tools["wishlist_add_item"].annotations.destructiveHint is False


async def test_every_tool_declares_all_four_annotation_hints():
    """A missing hint is not a neutral omission. The spec reads an absent
    destructiveHint as true, so a read tool that declares only readOnlyHint is
    still advertising itself as destructive to a host that takes the default at
    its word, and directories reject a surface that is partly annotated."""
    async with Client(build_server()) as client:
        tools = await client.list_tools()

    for tool in tools:
        for hint in (
            "readOnlyHint",
            "destructiveHint",
            "idempotentHint",
            "openWorldHint",
        ):
            value = getattr(tool.annotations, hint, None)
            assert isinstance(value, bool), f"{tool.name} leaves {hint} unset"


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


# --- arguments that came from somewhere else --------------------------------

# Tool arguments are chosen by the calling model, and the model reads item
# names, store notes, and bios that other people wrote. Every string that
# reaches a URL is therefore attacker-shaped, and httpx removes dot segments
# before a request goes out.


@respx.mock
@pytest.mark.parametrize(
    "tool",
    ["wishlist_get_profile", "wishlist_get_wishlist", "wishlist_get_gift_guide"],
)
async def test_a_username_cannot_escape_its_path_segment(call, tool):
    """Unencoded, `../../v1/profiles/me` is not a 404. It is a different
    endpoint, called with this user's token and reported to the model under the
    name of the tool that was invoked."""
    route = respx.route(method="GET").mock(return_value=ok({"username": "x"}))

    await call(tool, {"username": "../../v1/profiles/me"})

    assert route.called
    for sent in route.calls:
        raw = sent.request.url.raw_path
        # /api/v1/<collection>/<username> and nothing else.
        assert raw.count(b"/") == 4, raw
        assert raw.endswith(b"/..%2F..%2Fv1%2Fprofiles%2Fme"), raw


@respx.mock
async def test_an_invite_token_cannot_reach_another_endpoint(call):
    """A `?` in the token would start a query string and swallow the `/accept`
    this tool appends, turning an invite acceptance into any POST the user's own
    token can make."""
    escaped = respx.post(f"{API}/api/v1/circle/requests/5/accept").mock(
        return_value=ok({"request": {"username": "mallory", "status": "accepted"}})
    )
    route = respx.route(method="POST").mock(
        return_value=ok({"invite": {"inviter_username": "alice"}})
    )

    await call(
        "wishlist_accept_invite",
        {"invite_token": "x/../../circle/requests/5/accept?z="},
    )

    assert not escaped.called, "the token reached an endpoint of its own choosing"
    sent = route.calls.last.request
    assert sent.url.raw_path == (
        b"/api/v1/invites/x%2F..%2F..%2Fcircle%2Frequests%2F5%2Faccept%3Fz%3D/accept"
    )
    assert not sent.url.query


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


# --- parity with the wishlist API --------------------------------------------
#
# Everything below covers ground the API gained after this server split off, plus
# the three places where the old shaping reported something that was not true.


@respx.mock
async def test_the_gift_guide_carries_the_fields_a_gift_actually_turns_on(call):
    """Sizes alone do not choose a gift. dietary, interests, price comfort, and
    already_own each rule something in or out, and a field this server drops is a
    field the model never learns."""
    respx.get(f"{API}/api/v1/profiles/sarah").mock(
        return_value=ok(
            {
                "username": "sarah",
                "pronouns": "she/her",
                "birthday": {"month": 4, "day": 17},
                "ring_size": "6",
                "gift_format_preference": "experiences",
                "price_comfort": "under_50",
                "interests": [{"category": "collects", "value": "vinyl"}],
                "dietary": ["vegan", "no_alcohol"],
                "already_own": "a record player",
                "delivery_notes": "leave with the neighbour",
                "profile_reviewed_at": "2026-08-20T10:00:00Z",
                "hidden_from_you": [],
                "in_your_circle": True,
            }
        )
    )
    respx.get(f"{API}/api/v1/wishlists/sarah").mock(return_value=ok({"items": []}))

    profile = (await call("wishlist_get_gift_guide", {"username": "sarah"}))["profile"]

    assert profile["dietary"] == ["vegan", "no_alcohol"]
    assert profile["interests"] == [{"category": "collects", "value": "vinyl"}]
    assert profile["birthday"] == {"month": 4, "day": 17}
    assert profile["price_comfort"] == "under_50"
    assert profile["gift_format_preference"] == "experiences"
    assert profile["already_own"] == "a record player"
    assert profile["ring_size"] == "6"
    assert profile["pronouns"] == "she/her"
    assert profile["delivery_notes"] == "leave with the neighbour"
    assert profile["profile_reviewed_at"] == "2026-08-20T10:00:00Z"


@respx.mock
async def test_nobody_but_the_owner_is_handed_a_birth_year(call):
    """The API nulls birth_date for everyone else, and GiftProfile has no field
    for it either. Two layers, because a year is not recoverable once stated."""
    respx.get(f"{API}/api/v1/profiles/sarah").mock(
        return_value=ok(
            {
                "username": "sarah",
                "birth_date": None,
                "birthday": {"month": 4, "day": 17},
                "hidden_from_you": [],
                "in_your_circle": True,
            }
        )
    )

    profile = await call("wishlist_get_profile", {"username": "sarah"})

    assert "birth_date" not in profile
    assert profile["birthday"] == {"month": 4, "day": 17}


@respx.mock
async def test_my_own_profile_keeps_the_year_and_all_seventeen_keys(call):
    respx.get(f"{API}/api/v1/profiles/me").mock(
        return_value=ok(
            {
                "username": "me",
                "birth_date": "1990-04-17",
                "birthday": {"month": 4, "day": 17},
                "dietary": [],
                "interests": [],
                "visibility": {"address": "circle_only", "dietary": "public"},
            }
        )
    )

    profile = await call("wishlist_get_my_profile")

    assert profile["birth_date"] == "1990-04-17"
    assert profile["visibility"]["address"] == "circle_only"
    assert profile["dietary"] == [], "an empty list is an answer, not an absence"


@respx.mock
async def test_search_says_what_you_are_to_each_result(call):
    """Without relationship every row offers the same button, and the agent finds
    out only afterwards that it already had a request pending."""
    respx.get(f"{API}/api/v1/search/people").mock(
        return_value=ok(
            {
                "results": [
                    {
                        "username": "sarah",
                        "display_name": "Sarah",
                        "avatar_url": "https://img.example/a.png",
                        "relationship": "request_sent",
                    }
                ]
            }
        )
    )

    person = (await call("wishlist_search_people", {"query": "sar"}))[0]

    assert person["relationship"] == "request_sent"
    assert person["avatar_url"] == "https://img.example/a.png"


@respx.mock
async def test_a_result_the_api_says_nothing_about_is_not_claimed_as_a_stranger(call):
    """An older API build omits the key. Defaulting to "none" is the safe read
    only because it is also the one that offers to ask rather than to accept."""
    respx.get(f"{API}/api/v1/search/people").mock(
        return_value=ok({"results": [{"username": "sarah", "display_name": None}]})
    )

    assert (await call("wishlist_search_people", {"query": "sar"}))[0][
        "relationship"
    ] == "none"


# --- POST /invites answers two different things ------------------------------


@respx.mock
async def test_inviting_a_stranger_still_reports_an_email(call):
    respx.post(f"{API}/api/v1/invites").mock(
        return_value=ok(
            {
                "invite": {
                    "recipient_email": "new@example.com",
                    "expires_at": "2026-09-01T00:00:00Z",
                }
            }
        )
    )

    outcome = await call("wishlist_create_invite", {"email": "new@example.com"})

    assert outcome["outcome"] == "invite_emailed"
    assert outcome["email"] == "new@example.com"
    assert outcome["expires_at"] == "2026-09-01"


@respx.mock
async def test_inviting_an_address_that_has_an_account_does_not_claim_an_email(call):
    """The bug this replaced. POST /invites answers `request` when the address
    already belongs to someone, and reading only `invite` reported a sent email
    with a blank expiry for a message that was never sent."""
    respx.post(f"{API}/api/v1/invites").mock(
        return_value=ok(
            {
                "request": {
                    "id": 7,
                    "direction": "outgoing",
                    "status": "pending",
                    "username": "sarah",
                    "display_name": "Sarah",
                    "created_at": "2026-08-25T10:00:00Z",
                }
            }
        )
    )

    outcome = await call("wishlist_create_invite", {"email": "sarah@example.com"})

    assert outcome["outcome"] == "request_sent"
    assert outcome["username"] == "sarah"
    assert outcome["request_id"] == 7
    assert outcome["email"] is None, "no address was emailed"
    assert outcome["expires_at"] is None


@respx.mock
async def test_inviting_someone_who_already_asked_you_reports_the_join(call):
    respx.post(f"{API}/api/v1/invites").mock(
        return_value=ok(
            {
                "request": {
                    "id": 7,
                    "direction": "incoming",
                    "status": "accepted",
                    "username": "sarah",
                    "display_name": "Sarah",
                    "created_at": "2026-08-25T10:00:00Z",
                }
            }
        )
    )

    outcome = await call("wishlist_create_invite", {"email": "sarah@example.com"})

    assert outcome["outcome"] == "circle_joined"
    assert outcome["username"] == "sarah"


# --- circle requests ---------------------------------------------------------


@respx.mock
async def test_pending_requests_come_back_in_both_directions(call):
    respx.get(f"{API}/api/v1/circle/requests").mock(
        return_value=ok(
            {
                "incoming": [
                    {
                        "id": 1,
                        "direction": "incoming",
                        "status": "pending",
                        "user_id": 9,
                        "username": "sarah",
                        "display_name": "Sarah",
                        "created_at": "2026-08-25T10:00:00Z",
                    }
                ],
                "outgoing": [
                    {
                        "id": 2,
                        "direction": "outgoing",
                        "status": "pending",
                        "user_id": 10,
                        "username": "tom",
                        "display_name": None,
                        "created_at": "2026-08-24T10:00:00Z",
                    }
                ],
            }
        )
    )

    requests = await call("wishlist_list_circle_requests")

    assert [r["username"] for r in requests["incoming"]] == ["sarah"]
    assert [r["id"] for r in requests["outgoing"]] == [2]
    assert "user_id" not in requests["incoming"][0], "database keys mean nothing here"


@respx.mock
async def test_asking_someone_reports_a_pending_request(call):
    respx.post(f"{API}/api/v1/circle/requests").mock(
        return_value=ok(
            {
                "request": {
                    "id": 4,
                    "direction": "outgoing",
                    "status": "pending",
                    "username": "sarah",
                    "display_name": "Sarah",
                    "created_at": "2026-08-25T10:00:00Z",
                }
            }
        )
    )

    outcome = await call("wishlist_request_circle", {"username": "sarah"})

    assert outcome == {"outcome": "request_sent", "username": "sarah", "request_id": 4}


@respx.mock
async def test_asking_back_settles_it_and_says_so(call):
    """The API answers 200 with an accepted request when they had already asked.
    The client does not carry status codes up, so this reads the request's own
    status. Reporting "request_sent" here would hide that circle_only fields are
    already shared."""
    respx.post(f"{API}/api/v1/circle/requests").mock(
        return_value=ok(
            {
                "request": {
                    "id": 4,
                    "direction": "incoming",
                    "status": "accepted",
                    "username": "sarah",
                    "display_name": "Sarah",
                    "created_at": "2026-08-25T10:00:00Z",
                }
            }
        )
    )

    outcome = await call("wishlist_request_circle", {"username": "sarah"})

    assert outcome["outcome"] == "circle_joined"
    assert outcome["request_id"] is None


@respx.mock
async def test_accepting_and_declining_hit_their_own_endpoints(call):
    accept = respx.post(f"{API}/api/v1/circle/requests/3/accept").mock(
        return_value=ok({"request": {"username": "sarah", "status": "accepted"}})
    )
    decline = respx.post(f"{API}/api/v1/circle/requests/5/decline").mock(
        return_value=ok({"request": {"username": "tom", "status": "declined"}})
    )

    accepted = await call("wishlist_accept_circle_request", {"request_id": 3})
    declined = await call("wishlist_decline_circle_request", {"request_id": 5})

    assert accepted == {"outcome": "accepted", "username": "sarah"}
    assert declined == {"outcome": "declined", "username": "tom"}
    assert accept.called and decline.called


@respx.mock
async def test_a_refusal_from_the_api_reaches_the_model_as_a_sentence(call):
    respx.post(f"{API}/api/v1/circle/requests").mock(
        return_value=err(409, "request_already_pending", "You already asked Sarah.")
    )

    with pytest.raises(ToolError) as excinfo:
        await call("wishlist_request_circle", {"username": "sarah"})

    assert "already asked Sarah" in str(excinfo.value)


# --- removing a circle member ------------------------------------------------


@respx.mock
async def test_removing_a_member_resolves_the_username_first(call):
    """Every other tool addresses people by username. Taking a raw database id
    for the one destructive call is how the wrong person gets removed."""
    respx.get(f"{API}/api/v1/circle/members").mock(
        return_value=ok(
            {
                "members": [
                    {"user_id": 9, "username": "sarah", "display_name": "Sarah"},
                    {"user_id": 10, "username": "tom", "display_name": None},
                ]
            }
        )
    )
    delete = respx.delete(f"{API}/api/v1/circle/members/10").mock(return_value=ok(None))

    outcome = await call("wishlist_remove_circle_member", {"username": "tom"})

    assert outcome == {"removed": True, "username": "tom"}
    assert delete.called


@respx.mock
async def test_removing_someone_who_is_not_in_the_circle_deletes_nothing(call):
    respx.get(f"{API}/api/v1/circle/members").mock(
        return_value=ok({"members": [{"user_id": 9, "username": "sarah"}]})
    )
    delete = respx.delete(url__regex=rf"{API}/api/v1/circle/members/\d+").mock(
        return_value=ok(None)
    )

    with pytest.raises(ToolError) as excinfo:
        await call("wishlist_remove_circle_member", {"username": "tom"})

    assert "not in your circle" in str(excinfo.value)
    assert not delete.called


@respx.mock
async def test_the_circle_list_does_not_invent_a_join_date(call):
    """The members endpoint records none. An empty string dressed up as a date is
    worse than its absence, and the old shape shipped one on every row."""
    respx.get(f"{API}/api/v1/circle/members").mock(
        return_value=ok(
            {"members": [{"user_id": 9, "username": "sarah", "display_name": "Sarah"}]}
        )
    )

    member = (await call("wishlist_list_circle"))[0]

    assert member == {"username": "sarah", "display_name": "Sarah"}


# --- profile writes ----------------------------------------------------------


@respx.mock
async def test_the_new_profile_fields_reach_the_api(call):
    route = respx.patch(f"{API}/api/v1/profiles/me").mock(
        return_value=ok({"username": "me", "visibility": {}})
    )

    await call(
        "wishlist_update_my_profile",
        {
            "birth_date": "1990-04-17",
            "ring_size": "6",
            "price_comfort": "under_50",
            "gift_format_preference": "experiences",
            "interests": [{"category": "collects", "value": "vinyl"}],
            "dietary": ["vegan"],
            "delivery_notes": "leave with the neighbour",
            "already_own": "a record player",
        },
    )

    sent = json.loads(route.calls.last.request.content)
    assert sent["birth_date"] == "1990-04-17"
    assert sent["interests"] == [{"category": "collects", "value": "vinyl"}]
    assert sent["dietary"] == ["vegan"]
    assert sent["already_own"] == "a record player"
    assert sent["ring_size"] == "6"


@respx.mock
async def test_an_empty_dietary_list_is_sent_rather_than_dropped(call):
    """None means unanswered and [] means nothing applies. Dropping the empty
    list would leave a stale rule in place with no way to clear it."""
    route = respx.patch(f"{API}/api/v1/profiles/me").mock(
        return_value=ok({"username": "me", "visibility": {}})
    )

    await call("wishlist_update_my_profile", {"dietary": []})

    assert json.loads(route.calls.last.request.content) == {"dietary": []}


@respx.mock
async def test_fields_the_caller_left_alone_are_never_sent(call):
    """An explicit null would clear a field the user never mentioned."""
    route = respx.patch(f"{API}/api/v1/profiles/me").mock(
        return_value=ok({"username": "me", "visibility": {}})
    )

    await call("wishlist_update_my_profile", {"shirt_size": "L"})

    assert json.loads(route.calls.last.request.content) == {"shirt_size": "L"}


# --- the README is part of the interface --------------------------------------


async def test_the_readme_lists_exactly_the_tools_that_exist():
    """The table is what a reader trusts before connecting anything. A tool added
    without a row, or a row left behind after a rename, is the same class of drift
    this server keeps having with the API."""
    readme = (pathlib.Path(__file__).resolve().parents[1] / "README.md").read_text()
    documented = set(re.findall(r"^\| `(wishlist_\w+)`", readme, re.MULTILINE))

    async with Client(build_server()) as client:
        registered = {t.name for t in await client.list_tools()}

    assert documented == registered


# --- calendar, reminders, notifications --------------------------------------


@respx.mock
async def test_upcoming_occasions_carries_the_person_and_never_a_birth_year(call):
    respx.get(f"{API}/api/v1/calendar/upcoming").mock(
        return_value=ok(
            {
                "window_days": 90,
                "from_date": "2026-09-06",
                "to_date": "2026-12-05",
                "occasions": [
                    {
                        "kind": "birthday",
                        "key": "birthday:alice",
                        "date": "2026-09-14",
                        "days_away": 8,
                        "title": "Alice Rivera's birthday",
                        "person": {
                            "username": "alice",
                            "display_name": "Alice Rivera",
                            "avatar_url": None,
                        },
                        "wishlist_url": "/alice",
                    },
                    {
                        "kind": "holiday",
                        "key": "holiday:christmas",
                        "date": "2026-12-25",
                        "days_away": 110,
                        "title": "Christmas Day",
                        "person": None,
                        "wishlist_url": None,
                    },
                ],
            }
        )
    )

    result = _plain(await call("wishlist_upcoming_occasions"))

    assert result["from_date"] == "2026-09-06"
    birthday, holiday = result["occasions"]
    assert birthday["person"]["username"] == "alice"
    assert birthday["days_away"] == 8
    assert holiday["person"] is None
    # The API never sends one, and nothing here invents one.
    assert "1990" not in json.dumps(result)


@respx.mock
async def test_the_occasion_window_is_capped(call):
    route = respx.get(f"{API}/api/v1/calendar/upcoming").mock(
        return_value=ok({"from_date": "", "to_date": "", "occasions": []})
    )

    await call("wishlist_upcoming_occasions", {"days": 9000})

    assert route.calls.last.request.url.params["days"] == "365"


@respx.mock
async def test_listing_notifications_does_not_mark_them_read(call):
    listing = respx.get(f"{API}/api/v1/notifications").mock(
        return_value=ok(
            {
                "unread_count": 1,
                "notifications": [
                    {
                        "id": 7,
                        "kind": "occasion_reminder",
                        "title": "Alice Rivera's birthday is in 7 days",
                        "body": "14 September.",
                        "link_url": "/alice",
                        "payload": {"kind": "birthday"},
                        "read_at": None,
                        "created_at": "2026-09-07T09:00:00Z",
                    }
                ],
            }
        )
    )
    read_all = respx.post(f"{API}/api/v1/notifications/read-all").mock(
        return_value=ok({"marked_read": 1})
    )

    result = _plain(await call("wishlist_list_notifications"))

    assert result["unread_count"] == 1
    assert result["notifications"][0]["id"] == 7
    assert listing.called
    assert not read_all.called


@respx.mock
async def test_marking_one_read_addresses_that_id(call):
    route = respx.post(f"{API}/api/v1/notifications/7/read").mock(
        return_value=ok({"notification": {"id": 7}})
    )

    result = _plain(
        await call("wishlist_mark_notifications_read", {"notification_id": 7})
    )

    assert route.called
    assert result["marked_read"] == 1


@respx.mock
async def test_marking_all_read_reports_what_actually_moved(call):
    respx.post(f"{API}/api/v1/notifications/read-all").mock(
        return_value=ok({"marked_read": 0})
    )

    result = _plain(await call("wishlist_mark_notifications_read"))

    # Zero is success: they were already read.
    assert result["marked_read"] == 0


@respx.mock
async def test_reminder_preferences_fall_back_to_the_documented_defaults(call):
    """A response missing a key must not become False by accident."""
    respx.get(f"{API}/api/v1/reminders/preferences").mock(
        return_value=ok({"preferences": {}})
    )

    result = _plain(await call("wishlist_get_reminder_preferences"))

    assert result == {
        "email_enabled": True,
        "lead_days": [7, 1],
        "region": "US",
        "birthday_reminders": True,
        "holiday_reminders": True,
        "announce_birthday": True,
    }


@respx.mock
async def test_updating_preferences_sends_only_what_was_set(call):
    route = respx.patch(f"{API}/api/v1/reminders/preferences").mock(
        return_value=ok(
            {
                "preferences": {
                    "email_enabled": True,
                    "lead_days": [14, 1],
                    "region": "GB",
                    "birthday_reminders": True,
                    "holiday_reminders": True,
                    "announce_birthday": True,
                }
            }
        )
    )

    await call("wishlist_update_reminder_preferences", {"region": "GB"})

    assert json.loads(route.calls.last.request.content) == {"region": "GB"}


@respx.mock
async def test_updating_preferences_with_nothing_to_change_is_refused(call):
    route = respx.patch(f"{API}/api/v1/reminders/preferences")

    with pytest.raises(ToolError, match="Nothing to change"):
        await call("wishlist_update_reminder_preferences")

    assert not route.called


@respx.mock
async def test_an_unsupported_region_never_reaches_the_api(call):
    """Region is a Literal, so the client rejects it before a request goes out."""
    route = respx.patch(f"{API}/api/v1/reminders/preferences")

    with pytest.raises(ToolError):
        await call("wishlist_update_reminder_preferences", {"region": "ZZ"})

    assert not route.called
