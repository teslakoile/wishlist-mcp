from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WISHLIST_MCP_", case_sensitive=False, env_file=".env"
    )

    api_base_url: str = "http://localhost:8000"
    api_timeout: float = 15.0

    authkit_domain: str = ""
    # Every access token must carry exactly this as its audience, per RFC 8707,
    # and the protected resource metadata must echo it character for character.
    resource_uri: str = "http://localhost:9000/mcp"
    # The server 404s on any other host, so nobody reaches it by a name whose
    # audience will not match.
    host: str = "localhost:9000"

    def jwks_url(self) -> str:
        return f"{self.authkit_domain.rstrip('/')}/oauth2/jwks"

    def issuer(self) -> str:
        return self.authkit_domain.rstrip("/")

    def metadata_path(self) -> str:
        return "/.well-known/oauth-protected-resource/mcp"

    def metadata_url(self) -> str:
        """Derived from the resource URI so the two cannot drift apart."""
        origin = self.resource_uri.rsplit("/mcp", 1)[0]
        return f"{origin}{self.metadata_path()}"

    def mcp_path(self) -> str:
        """The path component of the resource URI, which is what we serve."""
        from urllib.parse import urlparse

        return urlparse(self.resource_uri).path or "/mcp"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
