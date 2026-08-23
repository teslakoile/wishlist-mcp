"""Building the MCP server itself."""

from fastmcp import FastMCP

from wishlist_mcp.config import settings
from wishlist_mcp.tools import register

INSTRUCTIONS = """\
wishlist keeps gift-ready profiles and wishlists, shared with an approved circle.

Choosing a gift for someone: call wishlist_get_gift_guide with their username,
which returns their sizes, preferences, allergies, things they do not want, and
their wishlist in one call. Use wishlist_search_people first if you only know
their name.

What you can see depends on whether the signed-in user is in that person's
circle. A profile field that is null and listed in hidden_from_you is private,
not empty, and wishlist items marked circle_only are omitted entirely.

Writes act on the signed-in user's own account only. Sending an invite emails a
real person and cannot be undone, and accepting one shares the signed-in user's
private fields with the inviter. Confirm both with the user first.
"""


def build_server() -> FastMCP:
    server = FastMCP(
        "wishlist",
        instructions=INSTRUCTIONS,
        version="1.0.0",
        website_url="https://app.wishlist.fit",
    )
    register(server)
    return server


def build_asgi_app():
    """The MCP server as an ASGI app owning the resource URI's path.

    Stateless with JSON responses, deliberately. Cloud Run runs several copies
    under load and shuts them all down when idle, so a session pinned to one
    instance breaks the moment the next request lands elsewhere.
    """
    return build_server().http_app(
        path=settings.mcp_path(),
        stateless_http=True,
        json_response=True,
        # The host guard rejects the wrong host with a 404 already, so FastMCP's
        # own protection would only duplicate it with a worse error.
        host_origin_protection=None,
        allowed_hosts=["*"],
    )
