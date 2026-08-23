# wishlist-mcp

The hosted MCP server for [wishlist.fit](https://app.wishlist.fit). It lets Claude,
ChatGPT, or Codex read and update your wishlist on your behalf.

```
https://mcp.wishlist.fit/mcp
```

Connect it from [app.wishlist.fit/connect](https://app.wishlist.fit/connect), which has
per-client setup steps. You need a wishlist account first; this server never creates one.

[docs/connecting.md](docs/connecting.md) has the longer version: exact commands for each
client, what has actually been verified against production, and the two Codex flags that
waste an afternoon if you get them wrong.

## What it is

A pure OAuth 2.1 **resource server** in front of the wishlist REST API. It holds no
database and no business rules. Visibility, rate limits, and invite handling all live in
the wishlist API, and this server inherits them by calling that API with the user's own
access token.

That split is deliberate. The alternative, a second service with its own copy of the
rules, is how two surfaces quietly start disagreeing about who may see what.

```
Claude / ChatGPT / Codex
        │  MCP over HTTP, bearer token
        ▼
   this server ──── verifies the token (issuer + audience)
        │
        │  the same token, forwarded
        ▼
   api.wishlist.fit ──── applies visibility, rate limits, invite rules
        │
        ▼
     Postgres
```

**WorkOS AuthKit** is the authorization server. This server issues nothing and shows no
consent screen; it only verifies what AuthKit signed.

## The tools

Fourteen, one per thing you can already do by hand in the web app. Parity is the rule:
no agent-only privileges, and nothing the app itself cannot do.

| Tool | Kind |
|---|---|
| `wishlist_search_people` | read |
| `wishlist_get_gift_guide` | read |
| `wishlist_get_profile` | read |
| `wishlist_get_wishlist` | read |
| `wishlist_get_my_profile` | read |
| `wishlist_get_my_wishlist` | read |
| `wishlist_list_circle` | read |
| `wishlist_preview_invite` | read |
| `wishlist_add_item` | write |
| `wishlist_update_item` | write |
| `wishlist_update_my_profile` | write |
| `wishlist_accept_invite` | write |
| `wishlist_create_invite` | **destructive** |
| `wishlist_delete_item` | **destructive** |

`wishlist_get_gift_guide` returns a person's profile and wishlist together. It is the
reason anyone connects this server, and without it the gift-giver journey costs three
round trips.

The two destructive tools carry `destructiveHint: true`, so clients that confirm
irreversible actions will ask first. Sending an invite emails a real person and cannot be
unsent. Accepting one is reciprocal: the inviter gains access to your circle-only fields
as well as you gaining access to theirs.

### One connection, everything

There are no scopes. WorkOS cannot express custom ones, so a connection carries the whole
tool surface. Protection on writes is behavioural rather than structural: the annotations
above, the tool descriptions, and the rate limits the API applies.

## Running it locally

```bash
uv sync
cp .env.example .env      # point it at a local API and the Local WorkOS environment
uv run python -m wishlist_mcp.main
```

```bash
uv run pytest
uv run ruff format --check . && uv run ruff check .
```

Tests stub the wishlist API with `respx`. What they check is this server's own job:
verifying tokens, shaping requests, and turning API errors into sentences a model can act
on. The rules themselves belong to the API and are tested there.

## Configuration

Every variable is prefixed `WISHLIST_MCP_`. See `.env.example`.

| Variable | Purpose |
|---|---|
| `API_BASE_URL` | The wishlist REST API to call |
| `AUTHKIT_DOMAIN` | WorkOS AuthKit issuer. Empty means the server refuses to start |
| `RESOURCE_URI` | Canonical URI. Every token's audience must match it exactly |
| `HOST` | The server 404s on any other host |
| `API_TIMEOUT` | Seconds to wait on the API |

`RESOURCE_URI` must match the resource indicator configured in WorkOS **character for
character**, including the `/mcp` path. A mismatch is the most common reason a client
refuses to connect.

## Things worth knowing before you change this

Each of these is a bug that reached production once.

- **`get_http_headers()` strips `authorization`.** Ask for it explicitly. Without that,
  every tool call reports "no access token" while `initialize` and `tools/list` still
  succeed, because those are answered before any tool body runs. A green handshake is not
  evidence the tools work.
- **The MCP app is mounted at the root and owns its own path.** Mounting it at `/mcp`
  makes Starlette redirect `/mcp` to `/mcp/`, and the address every client is handed has
  no trailing slash.
- **`X-Forwarded-Proto` is trusted.** Cloud Run terminates TLS, so without it every
  generated URL claims `http://`. Combined with the redirect above, that once meant a
  client would have sent its bearer token in the clear.
- **FastMCP's lifespan is chained into the app's.** Mounting an ASGI app does not start
  its lifespan, and without it every tool call fails with "Task group is not initialized"
  while unit tests still pass.
- **`TestClient` follows redirects by default.** Pass `follow_redirects=False` when the
  point is that a path answers directly.

## Deployment

Cloud Run, `asia-southeast1`, in the same project as the rest of wishlist. `main` deploys
on push. Infrastructure lives in `wishlist-infrastructure`.

## Licence

MIT. See [LICENSE](LICENSE).
