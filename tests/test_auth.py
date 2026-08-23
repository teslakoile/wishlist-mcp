"""Token verification.

The load-bearing test is the audience check. The MCP spec requires a resource
server to verify a token was issued for *itself*, not merely that it is valid.
Without that, any token AuthKit ever mints for any resource opens this server.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import ISSUER, RESOURCE, SUBJECT, make_token
from wishlist_mcp.auth import InvalidAccessToken, authenticate, bearer_token, verify


def test_a_token_for_another_resource_is_rejected(keypair):
    """RFC 8707. A token minted for a different resource is a token for a
    different service."""
    with pytest.raises(InvalidAccessToken) as excinfo:
        verify(make_token(keypair, aud="https://someone-elses.example.com/mcp"))

    assert "different resource" in excinfo.value.reason


def test_a_token_from_another_issuer_is_rejected(keypair):
    with pytest.raises(InvalidAccessToken) as excinfo:
        verify(make_token(keypair, iss="https://evil.example.com"))

    assert "different issuer" in excinfo.value.reason


def test_an_expired_token_is_rejected(keypair):
    with pytest.raises(InvalidAccessToken) as excinfo:
        verify(make_token(keypair, exp=datetime.now(UTC) - timedelta(minutes=1)))

    assert "expired" in excinfo.value.reason


def test_a_valid_token_returns_its_claims(keypair):
    claims = verify(make_token(keypair))

    assert claims["iss"] == ISSUER
    assert claims["aud"] == RESOURCE
    assert claims["sub"] == SUBJECT


def test_this_server_does_not_resolve_the_subject(keypair):
    """It holds no database. The wishlist API resolves the subject when the
    token is forwarded, which is why an unknown user is that API's 401 and not
    a lookup failure here."""
    claims = authenticate(f"Bearer {make_token(keypair, sub='nobody-at-all')}")

    assert claims["sub"] == "nobody-at-all"


@pytest.mark.parametrize(
    "header", [None, "", "Basic abc123", "Bearer", "Bearer   ", "token abc"]
)
def test_a_header_that_is_not_a_bearer_token_is_rejected(header):
    with pytest.raises(InvalidAccessToken):
        bearer_token(header)


def test_the_bearer_scheme_is_case_insensitive():
    assert bearer_token("bearer abc123") == "abc123"
