"""Entrypoint. Cloud Run sets PORT."""

import os

import uvicorn

from wishlist_mcp.app import create_app

app = create_app()


def run() -> None:
    uvicorn.run(
        "wishlist_mcp.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        # Cloud Run terminates TLS; ForwardedProto handles the scheme.
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    run()
