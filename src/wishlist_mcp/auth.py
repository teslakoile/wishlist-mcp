"""Verifying the access tokens AuthKit issued.

The MCP spec requires a resource server to check that a token was issued for
*itself*, per RFC 8707, not merely that it is a valid token from a trusted
issuer. A token minted for a different resource is a token for a different
service, and accepting it is the difference between an authorization boundary
and a suggestion.
"""

import jwt
from jwt import PyJWKClient

from wishlist_mcp.config import settings

# AuthKit signs with RS256.
ALGORITHMS = ["RS256"]

_jwks_client: PyJWKClient | None = None


class InvalidAccessToken(Exception):
    """The token is missing, malformed, expired, or issued for something else.

    Every case is a 401 with a pointer to the resource metadata, never a 403 and
    never a 404: the client's correct response is to obtain a new token, and the
    metadata tells it where.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def jwks_client() -> PyJWKClient:
    """Cached across requests. Refetching the key set per call would add a
    network round trip to every tool call and be rate-limited by AuthKit."""
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(settings.jwks_url(), cache_keys=True)
    return _jwks_client


def reset_jwks_client() -> None:
    """Drop the cached client. Only for tests, which point it at a fake key set."""
    global _jwks_client
    _jwks_client = None


def bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise InvalidAccessToken("no access token")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise InvalidAccessToken("authorization header is not a bearer token")
    return token.strip()


def verify(token: str) -> dict:
    """Validate signature, issuer, and audience, and return the claims."""
    try:
        signing_key = jwks_client().get_signing_key_from_jwt(token)
    except Exception as exc:
        raise InvalidAccessToken(f"could not resolve the signing key: {exc}") from exc

    try:
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=ALGORITHMS,
            issuer=settings.issuer(),
            audience=settings.resource_uri,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.InvalidAudienceError as exc:
        raise InvalidAccessToken(
            "this token was issued for a different resource"
        ) from exc
    except jwt.InvalidIssuerError as exc:
        raise InvalidAccessToken("this token came from a different issuer") from exc
    except jwt.ExpiredSignatureError as exc:
        raise InvalidAccessToken("this token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidAccessToken(f"invalid token: {exc}") from exc


def authenticate(authorization: str | None) -> dict:
    """Verify the header and return the claims.

    Unlike the wishlist API, this server does not resolve the subject to an
    account. It holds no database. The API does that when the token is forwarded.
    """
    return verify(bearer_token(authorization))
