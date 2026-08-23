"""The wishlist REST API, as seen from here.

This server owns no rules. Visibility, rate limits, duplicate invites, and who
may accept what all live in the wishlist API, and every call here carries the
user's own access token so the API applies them to the right person. That is the
point of the split: one implementation of the rules, two ways in.
"""

from typing import Any

import httpx
from fastmcp.exceptions import ToolError

from wishlist_mcp.config import settings

# Messages a model can act on, keyed by the API's error codes. Anything not
# listed falls back to the API's own message, which is already written for a
# person.
GUIDANCE = {
    "rate_limited": "{message}",
    "invite_limit_reached": "{message}",
    "global_invite_limit_reached": "{message}",
    "invite_already_pending": "{message}",
    "already_in_circle": "{message}",
    "cannot_invite_self": "{message}",
}


class WishlistAPI:
    """A thin, per-call client. One instance per tool invocation.

    Deliberately not a long-lived shared client: the credential belongs to
    whoever is calling, and a shared client is how one user's token ends up on
    another user's request.
    """

    def __init__(self, authorization: str):
        self._headers = {
            "authorization": authorization,
            "content-type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{settings.api_base_url.rstrip('/')}{path}"
        try:
            with httpx.Client(timeout=settings.api_timeout) as client:
                response = client.request(method, url, headers=self._headers, **kwargs)
        except httpx.HTTPError as exc:
            raise ToolError("Could not reach wishlist. Try again in a moment.") from exc

        if response.status_code == 204:
            return None

        try:
            body = response.json()
        except ValueError:
            body = {}

        if response.status_code >= 400:
            raise ToolError(_message_for(response.status_code, body))
        return body.get("data")

    def get(self, path: str, params: dict | None = None) -> Any:
        return self._request("GET", path, params=_clean(params))

    def post(self, path: str, json: dict | None = None) -> Any:
        return self._request("POST", path, json=json or {})

    def patch(self, path: str, json: dict) -> Any:
        return self._request("PATCH", path, json=json)

    def delete(self, path: str) -> Any:
        return self._request("DELETE", path)


def _message_for(status: int, body: dict) -> str:
    error = body.get("error") or {}
    code = error.get("code", "")
    message = error.get("message") or "wishlist rejected the request."

    if code in GUIDANCE:
        return GUIDANCE[code].format(message=message)
    if status == 401:
        return (
            "Not signed in to wishlist. Reconnect the wishlist server in your "
            "client settings."
        )
    if status == 404:
        return f"{message} Try wishlist_search_people to find the right username first."
    if status >= 500:
        return "wishlist is having trouble right now. Try again in a moment."
    return message


def _clean(params: dict | None) -> dict | None:
    """Drop None values so they do not become the string "None" in a query."""
    if not params:
        return None
    return {k: v for k, v in params.items() if v is not None}
