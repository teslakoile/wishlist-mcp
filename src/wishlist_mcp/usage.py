"""Telling the wishlist API that a tool was called.

The API counts what people do in the web app through its own event beacon. A
tool call never loads a page, so without this the people deciding what to build
next see the web app's numbers and nothing for this server, and read that as
nobody using it.

One event per tool call, however many API requests the tool makes. It carries the
tool's name, whether it worked, what kind of failure it was, and how long it
took. It never carries an argument or a result: those hold other people's names,
sizes, and wishlists, and none of that belongs in a usage count. The API enforces
the same list on its side, so a mistake here cannot widen it.

Sent with the caller's own token, like every other request this server makes.
That is how the API knows the event is a real tool call and not a browser
inventing one, and it is why there is still no service-wide credential.
"""

import logging
import time
from contextvars import ContextVar

import httpx
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware, MiddlewareContext

from wishlist_mcp.config import settings

logger = logging.getLogger(__name__)

EVENT_NAME = "mcp_tool_call"

# The failure a tool call ran into, written by the API client and read here.
# A dict rather than a plain value: FastMCP runs a synchronous tool in a worker
# thread with a copy of the context, so assigning the variable there would never
# be seen here, while mutating the object both copies point at is.
_failure: ContextVar[dict | None] = ContextVar("wishlist_tool_failure", default=None)


def note_failure(kind: str) -> None:
    """Record why the tool call in progress is failing. The first reason wins,
    since a later one is usually a consequence of it."""
    holder = _failure.get()
    if holder is not None:
        holder.setdefault("error", kind)


class UsageReporter(Middleware):
    """Report each tool call to the wishlist API after it finishes."""

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        if not settings.report_usage:
            return await call_next(context)

        holder: dict = {}
        token = _failure.set(holder)
        started = time.monotonic()
        failure: str | None = None
        try:
            return await call_next(context)
        except Exception as exc:
            failure = holder.get("error") or type(exc).__name__
            raise
        finally:
            _failure.reset(token)
            await _report(
                tool=context.message.name,
                failure=failure,
                ms=int((time.monotonic() - started) * 1000),
            )


async def _report(*, tool: str, failure: str | None, ms: int) -> None:
    """Post the event. Waited for, briefly, and never allowed to fail the tool.

    Waited for because Cloud Run stops giving an instance CPU once its response
    is sent, so a post left running in the background would mostly never leave.
    """
    try:
        authorization = get_http_headers(include={"authorization"}).get("authorization")
        if not authorization:
            return
        props: dict = {"tool": tool, "ok": failure is None, "ms": ms}
        if failure is not None:
            props["error"] = failure
        async with httpx.AsyncClient(timeout=settings.usage_timeout) as client:
            await client.post(
                f"{settings.api_base_url.rstrip('/')}/api/v1/events",
                headers={"authorization": authorization},
                json={"events": [{"name": EVENT_NAME, "props": props}]},
            )
    except Exception:
        logger.warning("could not report usage for %s", tool, exc_info=True)
