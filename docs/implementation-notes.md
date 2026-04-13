# Implementation Notes

Lessons learned and dead ends encountered while building this MCP server.

---

## Infrastructure: Lambda → AgentCore → App Runner

### Lambda + API Gateway

The initial architecture used a Lambda function behind API Gateway with a custom JWT authorizer. Removed because:

- API Gateway added complexity for what is essentially a pass-through to Secrets Manager
- Lambda cold starts conflicted with MCP's stateful SSE transport
- App Runner gives a persistent process with a direct HTTPS endpoint, which maps cleanly to Streamable HTTP MCP transport

### AWS Bedrock AgentCore

Attempted to use Bedrock AgentCore as the MCP hosting layer. Abandoned because:

- AgentCore requires `arm64` container images; our local builds (Apple Silicon cross-compiled for `linux/amd64`) caused a hard architecture conflict at deploy time
- AgentCore's IAM surface is significantly more complex (additional roles, trust policies, resource-based policies)
- The feature was in preview and the Terraform provider support was incomplete
- App Runner with ECR is simpler, faster to iterate on, and has no architecture constraints

All AgentCore Terraform resources (`bedrock_agentcore.tf`) and the Lambda file (`lambda.tf`) have been deleted. The IAM policy for Secrets Manager access was moved into `app_runner.tf`.

---

## Secret Value Entry: MCP Elicitation URL Mode

**References:**
- [MCP elicitation spec (draft)](https://modelcontextprotocol.io/specification/draft/client/elicitation)
- [Python SDK elicitation example](https://github.com/modelcontextprotocol/python-sdk/blob/main/examples/snippets/servers/elicitation.py)

### What the spec says

MCP spec 2025-11-25 defines two elicitation modes:

- **Form mode** (`ctx.elicit(schema=...)`) — server sends a JSON schema, client renders an inline form, the user's response travels back through the MCP protocol as a JSON-RPC message. The spec explicitly prohibits this for passwords/API keys.
- **URL mode** (`ctx.elicit_url(url=...)`) — server sends a URL, client opens it in the browser, user interacts with a page hosted by the server. The value never enters the MCP protocol.

The Python SDK (v1.27.0) implements both: `ctx.elicit()` for form mode and `ctx.elicit_url()` for URL mode, plus `session.send_elicit_complete()` to notify the client when the out-of-band action completes.

### Why form mode is not acceptable for secrets

With form mode the value is present in the JSON-RPC `elicitation/response` message. Even though it's encrypted in transit (HTTPS), it surfaces in several places:

- **MCP client logs** — clients log MCP traffic; the elicitation response containing the value appears there
- **Client process memory** — the client holds the value between receiving the form submission and forwarding it to the server
- **Proxy/debugging tools** — enterprise proxies (mitmproxy, Charles) that terminate TLS would see the value in the decrypted JSON body
- **Client implementation trust** — you're trusting that the closed-source client doesn't store or forward the value; this can't be verified

URL mode avoids all of this: the value goes browser → server via a direct HTTPS POST and never appears in any MCP message.

### What clients actually support (as of April 2026)

| Client | Form mode | URL mode |
|---|---|---|
| Claude Code 2.1.104 | ✅ | ❌ — returns `"Client does not support URL-mode elicitation requests"` |
| Cursor 3.0 | ✅ (buggy in v1.6+) | ❌ — not implemented |
| VS Code Copilot 1.107+ | ✅ | ✅ — reportedly supported, unverified |

URL mode is in the spec and the SDK but neither Claude Code nor Cursor have shipped client-side support.

### What we implemented instead

`create_secret` and `update_secret` return a one-time URL. The user opens it manually in their browser. The form POSTs directly to App Runner, which calls Secrets Manager. The secret value never passes through Claude or the MCP protocol.

This achieves the same security properties as URL mode elicitation — the only thing missing is the client automatically opening the browser and showing an inline Accept/Decline prompt.

A `finalize_secret(token)` tool was added at one point to let Claude confirm the operation completed, but was later removed — the form POST handler can call Secrets Manager directly and show the result on the success page, with no second tool call needed.

The in-memory token store (`_pending_ops`) requires App Runner to run as a single instance (`max_size=1` in the auto-scaling config). A form POST landing on a different instance than the one that generated the token would return "link expired."

---

## OAuth Redirect URIs

Every MCP client uses a different redirect URI for OAuth callbacks. Each one needs to be registered individually in Okta.

| Client | Redirect URI | Notes |
|---|---|---|
| Claude Code | `http://localhost:3000/callback` | Port configurable via `"callbackPort"` in mcp.json |
| Cursor (mcp-remote) | Random ephemeral port | Broken — port changes on every auth attempt |
| Cursor native HTTP (v0.48+) | `cursor://anysphere.cursor-mcp/oauth/callback` | Custom OS scheme; requires IT to register the `cursor://` URI |
| VS Code Copilot | `http://127.0.0.1:33418/` | Must use `127.0.0.1` not `localhost`; trailing slash required |

### Cursor RFC 8414 path-stripping bug

Cursor strips path components from the `issuer` URL when independently re-fetching OAuth discovery metadata. Given:

```
issuer: https://myfox.okta.com/oauth2/default
```

Cursor fetches `https://myfox.okta.com/.well-known/openid-configuration` (org-level) instead of `https://myfox.okta.com/oauth2/default/.well-known/openid-configuration` (custom authorization server). It gets the wrong token endpoint, then Okta returns:

```
The grant was issued for another authorization server
```

Workaround (not yet implemented): set `issuer` in the OAuth metadata to the App Runner base URL and add a `/oauth/token` proxy endpoint that forwards to the real Okta token endpoint.
