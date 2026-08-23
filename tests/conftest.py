"""Shared fixtures.

The wishlist API is stubbed with respx rather than reached. What these tests
check is this server's own job: verifying tokens, shaping requests, and turning
API errors into sentences a model can act on. The rules themselves belong to the
API and are tested there.
"""

import base64
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from wishlist_mcp import auth as auth_module
from wishlist_mcp.config import settings

ISSUER = "https://potent-lavender-04.authkit.app"
RESOURCE = "https://mcp.wishlist.fit/mcp"
HOST = "mcp.wishlist.fit"
API = "https://api.wishlist.fit"
SUBJECT = "2482a28b-7102-4631-bba5-1b3fb3198126"


@pytest.fixture(scope="session")
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


@pytest.fixture(autouse=True)
def configured(monkeypatch, keypair):
    monkeypatch.setattr(settings, "authkit_domain", ISSUER)
    monkeypatch.setattr(settings, "resource_uri", RESOURCE)
    monkeypatch.setattr(settings, "host", HOST)
    monkeypatch.setattr(settings, "api_base_url", API)

    _, public = keypair

    class FakeKey:
        key = public

    class FakeJWKS:
        def get_signing_key_from_jwt(self, _token):
            return FakeKey()

    auth_module.reset_jwks_client()
    with patch.object(auth_module, "jwks_client", return_value=FakeJWKS()):
        yield
    auth_module.reset_jwks_client()


def make_token(keypair, **overrides) -> str:
    private, _ = keypair
    claims = {
        "sub": SUBJECT,
        "iss": ISSUER,
        "aud": RESOURCE,
        "exp": datetime.now(UTC) + timedelta(hours=1),
        "iat": datetime.now(UTC),
    }
    claims.update(overrides)
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(claims, pem, algorithm="RS256")


@pytest.fixture
def token(keypair) -> str:
    return make_token(keypair)


@pytest.fixture
def bearer(token) -> str:
    return f"Bearer {token}"


def decode(token: str) -> dict:
    import json

    return json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
