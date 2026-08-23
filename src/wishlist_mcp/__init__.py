"""Hosted MCP server for wishlist.fit.

A pure OAuth 2.1 resource server in front of the wishlist REST API. It holds no
database and no business rules: visibility, rate limits, and invite handling all
live in the wishlist API, and this server inherits them by calling it with the
user's own token.
"""

__version__ = "1.0.0"
