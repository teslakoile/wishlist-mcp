"""Discovery, the host guard, and the canonical path.

Every test here corresponds to a failure seen in production while this server
still lived inside the wishlist backend. They came across with the code because
the mistakes are properties of the transport, not of where it is hosted.
"""

import httpx
import pytest
import respx
from starlette.testclient import TestClient

from tests.conftest import API, HOST, ISSUER, RESOURCE
from wishlist_mcp.app import create_app


@pytest.fixture
def client():
    with TestClient(create_app(), base_url=f"https://{HOST}") as c:
        yield c


def rpc(method, params=None, request_id=1):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }


MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


# --- discovery ---------------------------------------------------------------


def test_metadata_names_the_canonical_resource_exactly(client):
    """`resource` must match character for character. A mismatch is the most
    common reason a client refuses to connect."""
    body = client.get("/.well-known/oauth-protected-resource/mcp").json()

    assert body["resource"] == RESOURCE
    assert body["authorization_servers"] == [ISSUER]
    assert body["bearer_methods_supported"] == ["header"]


def test_the_metadata_is_readable_without_a_token(client):
    """Discovery cannot start otherwise."""
    assert client.get("/.well-known/oauth-protected-resource/mcp").status_code == 200


def test_an_unauthenticated_call_points_at_the_metadata(client):
    """A client that knows only the address learns where to authorize from this
    header. Without it there is nowhere to go and the connection just fails."""
    response = client.post("/mcp", json={}, follow_redirects=False)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == (
        f'Bearer resource_metadata="https://{HOST}'
        '/.well-known/oauth-protected-resource/mcp"'
    )


@pytest.mark.parametrize("header", ["Bearer not-a-jwt", "Basic abc", ""])
def test_a_bad_token_gets_the_same_pointer(client, header):
    response = client.post("/mcp", json={}, headers={"Authorization": header})

    assert response.status_code == 401
    assert "resource_metadata" in response.headers["WWW-Authenticate"]


# --- the canonical path ------------------------------------------------------


def test_the_canonical_path_does_not_redirect(client, bearer):
    """Clients are handed the address with no trailing slash. Mounting the MCP
    app at "/mcp" makes Starlette redirect that to "/mcp/", and in production
    the redirect came back as http://, so following it would have carried a
    bearer token in the clear."""
    response = client.post(
        "/mcp",
        json=rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
        ),
        headers={"Authorization": bearer, **MCP_HEADERS},
        follow_redirects=False,
    )

    assert response.status_code != 307, "the canonical path must not redirect"
    assert response.status_code == 200, response.text
    assert response.json()["result"]["serverInfo"]["name"] == "wishlist"


def test_a_handshake_lists_all_fourteen_tools(client, bearer):
    """Exercises the mount and the chained lifespan together. FastMCP's session
    manager runs in its own lifespan, and mounting an ASGI app does not start
    it, so without the chaining every call fails with "Task group is not
    initialized" while unit tests still pass."""
    client.post(
        "/mcp",
        json=rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
        ),
        headers={"Authorization": bearer, **MCP_HEADERS},
    )
    response = client.post(
        "/mcp",
        json=rpc("tools/list", request_id=2),
        headers={"Authorization": bearer, **MCP_HEADERS},
    )

    assert response.status_code == 200, response.text
    assert len(response.json()["result"]["tools"]) == 14


@respx.mock
def test_a_real_tool_call_over_http_reaches_the_api(client, bearer):
    """The end-to-end path. FastMCP's get_http_headers() strips `authorization`
    by default, which once left every tool call reporting "no access token"
    while initialize and tools/list still worked, because those are answered
    before any tool body runs."""
    respx.get(f"{API}/api/v1/profiles/me").mock(
        return_value=httpx.Response(
            200, json={"data": {"username": "alice", "visibility": {}}}
        )
    )

    response = client.post(
        "/mcp",
        json=rpc("tools/call", {"name": "wishlist_get_my_profile", "arguments": {}}),
        headers={"Authorization": bearer, **MCP_HEADERS},
        follow_redirects=False,
    )

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert not result.get("isError"), result
    assert result["structuredContent"]["username"] == "alice"


# --- the host guard ----------------------------------------------------------


def test_every_route_404s_on_another_host(bearer):
    """Reaching the server by another name would give a working endpoint that
    then rejects every token for an audience mismatch."""
    with TestClient(create_app(), base_url="https://api.wishlist.fit") as other:
        assert other.get("/.well-known/oauth-protected-resource/mcp").status_code == 404
        assert other.post("/mcp", json={}).status_code == 404


def test_the_guard_names_where_the_server_actually_lives():
    with TestClient(create_app(), base_url="https://elsewhere.example.com") as other:
        assert RESOURCE in other.get("/anything").json()["error"]["message"]


# --- operations --------------------------------------------------------------


def test_health_answers_without_a_token(client):
    """Deliberately not /healthz: Cloud Run's frontend answers that path itself
    and the request never reaches the container."""
    assert client.get("/_health").json() == {"status": "ok"}


def test_the_server_refuses_to_start_without_an_issuer(monkeypatch):
    """It verifies tokens. With no issuer it cannot, and a server that accepts
    everything is worse than one that will not boot."""
    from wishlist_mcp.config import settings

    monkeypatch.setattr(settings, "authkit_domain", "")

    with pytest.raises(RuntimeError, match="AUTHKIT_DOMAIN"):
        create_app()


def test_x_forwarded_proto_is_trusted():
    """Cloud Run terminates TLS and forwards plain HTTP, so without this every
    URL the app generates claims http://."""
    import asyncio

    from wishlist_mcp.middleware import ForwardedProto

    seen = {}

    async def app(scope, receive, send):
        seen["scheme"] = scope["scheme"]

    asyncio.run(
        ForwardedProto(app)(
            {
                "type": "http",
                "scheme": "http",
                "path": "/mcp",
                "headers": [(b"x-forwarded-proto", b"https")],
            },
            None,
            None,
        )
    )

    assert seen["scheme"] == "https"


# --- the Accept header -------------------------------------------------------


HANDSHAKE = rpc(
    "initialize",
    {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"},
    },
)


def initialize(client, bearer, accept):
    """One handshake, with whatever Accept the caller wants to test."""
    headers = {"Authorization": bearer}
    if accept is not None:
        headers["Accept"] = accept
    return client.post("/mcp", json=HANDSHAKE, headers=headers)


@pytest.mark.parametrize(
    "accept",
    ["*/*", "application/*", "*/*;q=0.8", None, "", "  "],
    ids=["star", "application-star", "star-with-q", "absent", "empty", "blank"],
)
def test_an_accept_that_admits_json_reaches_the_tools(client, bearer, accept):
    """FastMCP matches Accept by substring, so a client that sends `*/*` gets
    told to accept application/json, which it already does. Plain curl sends
    `*/*`, so this is the first thing anyone hits."""
    response = initialize(client, bearer, accept)

    assert response.status_code == 200, response.text
    assert response.json()["result"]["serverInfo"]["name"] == "wishlist"


def test_a_named_json_accept_is_left_alone(client, bearer):
    """The spec-correct header must keep working untouched."""
    response = initialize(client, bearer, "application/json, text/event-stream")

    assert response.status_code == 200, response.text


def test_a_client_that_only_reads_sse_is_still_refused(client, bearer):
    """Widening is only for headers that already admit JSON. This one does not,
    and its 406 is the right answer, not a bug to paper over."""
    response = initialize(client, bearer, "text/event-stream")

    assert response.status_code == 406


def test_a_wildcard_accept_without_a_token_still_gets_401(client):
    """Ordering check. Widening sits inside the auth gate, so an anonymous
    caller learns where to authorize instead of hearing about a header."""
    response = client.post("/mcp", json=HANDSHAKE, headers={"Accept": "*/*"})

    assert response.status_code == 401
    assert "resource_metadata" in response.headers["WWW-Authenticate"]
