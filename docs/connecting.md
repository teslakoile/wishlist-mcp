# Connecting a client

The server address is the same everywhere:

```
https://mcp.wishlist.fit/mcp
```

You need a wishlist account before you start. The server never creates one for
you. Sign up at [app.wishlist.fit](https://app.wishlist.fit).

The connection carries your own identity. A connected AI can do what you can do
in the app and nothing more: it sees what you are allowed to see, and it writes
only to your own wishlist and your own circle.

## What was verified, and when

Each row below was confirmed with a real tool call against production, not with
a successful handshake. A handshake proves discovery and nothing else.

| Client | Registration | Verified | Version tested |
|---|---|---|---|
| ChatGPT (web) | DCR | 2026-08-23 | Developer mode, 5.6 Sol |
| Claude Code | CIMD | 2026-08-23 | 2.0.x |
| Codex CLI | DCR | 2026-08-23 | 0.149.0 |
| Claude (claude.ai, desktop) | CIMD | not tested | — |

## ChatGPT, in the browser

ChatGPT only accepts a custom MCP server when Developer mode is on. That switch
is account-wide and applies to every connector you add, not just this one.
ChatGPT labels it elevated risk for that reason. Turn it on deliberately.

1. Open **Settings**, then **Security and login**.
2. Under **Developer mode**, turn **Developer mode** on.
3. Go to **Settings**, then **Plugins**, then **Browse plugins**.
4. Choose the **+** button at the top right of the plugins page.
5. Fill the dialog in:
   - **Name**: `Wishlist`
   - **Connection**: leave it on **Server URL** and paste
     `https://mcp.wishlist.fit/mcp`
   - **Authentication**: **OAuth**
6. Tick **I understand and want to continue**, then choose **Create**.
7. Choose **Sign in with Wishlist** and finish the sign-in that opens.
8. If **Actions** still says "No app actions available yet", choose **Refresh**
   under **Information**. Discovery can lag the connection by a few seconds.

Connectors are called Plugins in the current build. If you are following an
older guide that says Connectors, it is the same screen.

## Claude Code

```bash
claude mcp add --transport http --scope user wishlist https://mcp.wishlist.fit/mcp
```

```bash
claude mcp login wishlist
```

Confirm it worked:

```bash
claude mcp get wishlist
```

You want `Status: ✔ Connected`.

Two things to know. `claude mcp login` needs a real terminal, so run it in your
own shell rather than through a script or a CI step. And the tools appear in
sessions you start after the login, not in the session you are already in.

Claude Code registers through CIMD, so its `client_id` is a URL
(`https://claude.ai/oauth/claude-code-client-metadata`) rather than a client
this server issued. Nothing is registered on our side.

## Codex

```bash
codex mcp add wishlist --url https://mcp.wishlist.fit/mcp
```

```bash
codex mcp login wishlist
```

Then confirm with an actual call, because `codex mcp list` showing `OAuth` only
means the server uses OAuth:

```bash
codex exec "Call wishlist_get_my_profile and print the result."
```

Two traps, both of which cost real time to find:

**Use Codex 0.149.0 or later.** On 0.141.0 the login prints
`Successfully logged in` and every tool call then dies with
`rmcp::transport::worker: worker quit with fatal: Transport channel closed, when
Auth(AuthorizationRequired)`. The credentials are fine. The client is not.

**Do not pass `--oauth-resource`.** Codex reads the `resource` value out of this
server's RFC 9728 metadata by itself. Passing the flag as well puts `resource`
in the authorization URL twice, and the authorization server rejects the whole
request with `invalid_query_params`.

## Claude, on claude.ai or the desktop app

Open **Settings**, then **Connectors**, then **Add custom connector**, and paste
the address. This path is untested. It uses the same CIMD registration that
Claude Code uses, so it is expected to work.

## When it does not connect

The address has to end in `/mcp`. Dropping it is the most common mistake.

The first request after an idle period is slow, because the service scales to
zero and has to start. If a first connection attempt times out, try once more.

Errors surface inside the AI app, not on the wishlist site. Look there for the
message.

`GET /mcp` answers `405` when you send a valid token and `401` when you send
none. Both are correct. This server speaks stateless JSON and offers no SSE
stream, so there is nothing to get. A client that treats the `401` on its
unauthenticated discovery probe as fatal has a bug in the client.

An `Accept` header of `*/*`, or none at all, works. It used to come back as
`406 Client must accept application/json`, which was wrong: both already accept
JSON. Sending exactly `text/event-stream` is still refused, because this server
answers in JSON and that client cannot read it.

A CLI that says it logged in successfully has not proved anything. Make a real
tool call before you believe it.

## What a connection can reach

Fourteen tools. They read your profile and wishlist including the parts you keep
private, read other people's profiles and wishlists exactly as the website would
show them to you, add, change, and delete items on your own wishlist, and send
and accept circle invites.

Two of those write to the world outside your account. Sending an invite emails a
real person and cannot be unsent. Accepting an invite lets that person see
everything you have marked circle-only. Both are marked destructive in the tool
metadata, so a well-behaved client asks you first.

Rate limits apply per account: 60 tool calls a minute and 20 invites a day.

## Disconnecting

Removing the connector inside Claude, ChatGPT, or Codex stops that client using
its token. The grant itself stays alive at the authorization server until it
expires, and the wishlist app has no way to kill it. Treat removal as "this app
stops asking", not as revocation.
