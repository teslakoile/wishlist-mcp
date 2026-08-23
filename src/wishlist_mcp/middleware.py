"""The layers in front of the tool mount.

Each exists because of a specific failure seen in production, and the comments
say which.
"""

from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from wishlist_mcp.auth import InvalidAccessToken, authenticate
from wishlist_mcp.config import settings


def unauthorized() -> JSONResponse:
    """401 carrying the pointer a client needs to start the OAuth flow.

    Without the resource_metadata parameter a client has nowhere to go and
    simply fails, which reads to the user as "the server is broken".
    """
    return JSONResponse(
        status_code=401,
        content={
            "error": {
                "code": "unauthorized",
                "message": "This MCP server requires a wishlist account. "
                "Your client should start the OAuth flow.",
            }
        },
        headers={
            "WWW-Authenticate": (f'Bearer resource_metadata="{settings.metadata_url()}"')
        },
    )


class ForwardedProto:
    """Trust X-Forwarded-Proto so generated URLs keep their scheme.

    Cloud Run terminates TLS and forwards plain HTTP, so the app sees
    scheme="http" and builds redirects on http://. That once turned a redirect
    on this endpoint into a plaintext downgrade carrying a bearer token.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            forwarded = header(scope, b"x-forwarded-proto")
            if forwarded:
                scope = dict(scope)
                scope["scheme"] = forwarded.split(",")[0].strip()
        await self.app(scope, receive, send)


class HostGuard:
    """404 on any host but the canonical one.

    Without this, reaching the server by another name gives a working endpoint
    that then rejects every token for an audience mismatch, which is a far more
    confusing failure than "not found".
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        got = (header(scope, b"host") or "").split(":")[0].lower()
        expected = settings.host.split(":")[0].lower()
        if got != expected:
            await JSONResponse(
                status_code=404,
                content={
                    "error": {
                        "code": "not_found",
                        "message": f"The MCP server lives at {settings.resource_uri}.",
                    }
                },
            )(scope, receive, send)
            return
        await self.app(scope, receive, send)


class AuthGate:
    """Reject an unauthenticated call to the tool mount before FastMCP sees it.

    The tools authenticate again, which is where per-call identity comes from.
    This gate exists for a different reason: a client holding no token needs a
    401 carrying the resource_metadata pointer, and it needs it at the transport
    level, before any MCP protocol negotiation.

    Deliberately does not cover the metadata route, which must stay readable
    without a token or discovery cannot start.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _is_tool_mount(scope["path"]):
            await self.app(scope, receive, send)
            return
        try:
            authenticate(header(scope, b"authorization"))
        except InvalidAccessToken:
            await unauthorized()(scope, receive, send)
            return
        await self.app(scope, receive, send)


class WildcardAccept:
    """Spell out an Accept header that already admits JSON, so FastMCP sees it.

    FastMCP matches Accept by substring, so it answers `406 Client must accept
    application/json` to a client that sends `*/*` and therefore already does.
    An absent header means the same thing under RFC 9110 and fails the same way.
    Plain `curl` sends `*/*`, so the first thing anyone tries against this
    server is the thing that looks broken.

    Only widened ranges are rewritten. A client that asks for exactly
    `text/event-stream` really cannot read our JSON, and its 406 is correct.
    """

    WANTED = b"application/json, text/event-stream"

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _is_tool_mount(scope["path"]):
            await self.app(scope, receive, send)
            return

        if _admits_json_only_by_wildcard(header(scope, b"accept")):
            headers = [(k, v) for k, v in scope["headers"] if k != b"accept"]
            headers.append((b"accept", self.WANTED))
            scope = {**scope, "headers": headers}
        await self.app(scope, receive, send)


def _admits_json_only_by_wildcard(accept: str | None) -> bool:
    """True when the header accepts JSON but never names it.

    Absent or empty counts: RFC 9110 reads that as accepting anything.
    """
    if not accept or not accept.strip():
        return True
    ranges = {part.split(";")[0].strip().lower() for part in accept.split(",")}
    if "application/json" in ranges:
        return False
    return bool(ranges & {"*/*", "application/*"})


def _is_tool_mount(path: str) -> bool:
    mcp_path = settings.mcp_path()
    return path == mcp_path or path.startswith(f"{mcp_path}/")


def header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None
