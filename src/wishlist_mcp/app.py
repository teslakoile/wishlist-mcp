"""The ASGI application.

A Starlette app holding three things: the RFC 9728 metadata a client needs to
discover where to authorize, a health check, and the MCP tool mount.
"""

from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from wishlist_mcp.config import settings
from wishlist_mcp.middleware import AuthGate, ForwardedProto, HostGuard, WildcardAccept
from wishlist_mcp.server import build_asgi_app


async def protected_resource_metadata(_request) -> JSONResponse:
    """RFC 9728 metadata.

    `resource` must equal the canonical URI character for character, trailing
    path included. A mismatch is the most common reason a client refuses to
    connect.
    """
    return JSONResponse(
        {
            "resource": settings.resource_uri,
            "authorization_servers": [settings.issuer()],
            "bearer_methods_supported": ["header"],
        }
    )


async def health(_request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def create_app() -> Starlette:
    if not settings.authkit_domain:
        raise RuntimeError(
            "WISHLIST_MCP_AUTHKIT_DOMAIN is not set. This server verifies tokens "
            "and cannot do so without an issuer."
        )

    mcp_app = build_asgi_app()

    @asynccontextmanager
    async def lifespan(app):
        # FastMCP's session manager runs in its own lifespan. Mounting an ASGI
        # app does not start it, so it has to be chained here or every tool call
        # fails with "Task group is not initialized" while the handshake still
        # looks healthy.
        async with mcp_app.lifespan(app):
            yield

    app = Starlette(
        routes=[
            Route(settings.metadata_path(), protected_resource_metadata),
            # Not /healthz: Cloud Run's frontend answers that path itself and
            # the request never reaches the container.
            Route("/_health", health),
            # Mounted at the root with the MCP app owning its own path. Mounting
            # at the path instead makes Starlette redirect /mcp to /mcp/, and
            # the address every client is handed has no trailing slash.
            Mount("", app=mcp_app),
        ],
        lifespan=lifespan,
    )

    # Applied inside out: the scheme is fixed first, then the host is checked,
    # then a token is demanded, and last a wildcard Accept is spelled out. That
    # last step sits innermost on purpose, so an unauthenticated call still gets
    # its 401 rather than a 406 about a header it was never going to be asked
    # about.
    return ForwardedProto(HostGuard(AuthGate(WildcardAccept(app))))
