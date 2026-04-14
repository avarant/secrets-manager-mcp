# Secrets Manager MCP Server

An MCP server for AWS Secrets Manager, hosted on App Runner with Okta OAuth 2.1 PKCE authentication.

Secret values never pass through the LLM or the MCP protocol. When creating or updating a secret, the server returns a one-time browser URL. The user enters the value in a form hosted on the server, which calls AWS Secrets Manager directly.

## Architecture

```
MCP Client (Claude Code / Cursor / VS Code)
    │  Streamable HTTP + Bearer JWT
    ▼
AWS App Runner  (server/main.py)
    │  JWT verified via Okta JWKS
    │  Group-based access via forge_groups claim
    ├─ /mcp              → MCP tools
    ├─ /secret-entry/{t} → Browser form for secret values
    └─ /.well-known/…    → OAuth metadata (RFC 8414)
    │
    ▼
AWS Secrets Manager
```

**Auth:** Okta OAuth 2.1 PKCE. The server exposes static DCR (`/register`) returning the pre-configured Okta client ID, so any MCP client can auto-discover and authenticate without manual configuration.

**Secret entry:** `create_secret` and `update_secret` return a one-time URL. The user opens it in their browser, enters the value, and submits. The form POSTs directly to App Runner, which stores it in Secrets Manager. The value is never returned to the MCP client. This replicates the security properties of [MCP URL mode elicitation](https://modelcontextprotocol.io/specification/draft/client/elicitation) — the approach the spec mandates for sensitive values — without requiring client-side support, which no major client has shipped as of April 2026. See `docs/implementation-notes.md` for details.

**Access control:** Secrets can be tagged with `mcp:read_groups` and `mcp:write_groups` (comma-separated Okta group names). Untagged secrets are accessible to all authenticated users.

## Prerequisites

- AWS CLI with SSO configured
- Terraform >= 1.6
- Docker (for building the container image)
- An Okta tenant with a custom authorization server

## Deploying

```bash
# Copy and fill in your values
cp .env.example infra/terraform.tfvars   # then edit manually

# Or use deploy.sh
./deploy.sh \
  --okta-issuer   https://your-tenant.okta.com/oauth2/your-auth-server-id \
  --okta-audience api://secrets-mcp

# After first deploy, trigger App Runner to pull the new image
aws apprunner start-deployment --service-arn <arn>
```

Terraform outputs the MCP endpoint and ready-to-use client configs.

> **Note:** `auto_deployments_enabled = false` in App Runner. After `terraform apply` you must manually trigger a deployment with `aws apprunner start-deployment` for the new image to go live.

## Connecting MCP Clients

### Claude Code

Add to your project `.mcp.json` or `~/.claude.json`:

```json
{
  "mcpServers": {
    "secrets-manager": {
      "type": "http",
      "url": "https://<your-app-runner-url>/mcp",
      "oauth": { "callbackPort": 3000 }
    }
  }
}
```

Register `http://localhost:3000/callback` as a redirect URI in your Okta app.

### Cursor

Cursor native HTTP (v0.48+) in `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "secrets-manager": {
      "url": "https://<your-app-runner-url>/mcp"
    }
  }
}
```

Register `cursor://anysphere.cursor-mcp/oauth/callback` as a redirect URI in your Okta app.

> **Known issue:** Cursor strips the path from the `issuer` URL when re-fetching OAuth discovery, causing token exchange to fail with "The grant was issued for another authorization server." See `docs/implementation-notes.md`.

### VS Code Copilot

Create `.vscode/mcp.json` in your workspace:

```json
{
  "servers": {
    "secrets-manager": {
      "type": "http",
      "url": "https://<your-app-runner-url>/mcp"
    }
  }
}
```

Register `http://127.0.0.1:33418/` as a redirect URI in your Okta app (note: `127.0.0.1`, not `localhost`; trailing slash required).

### Replit

Add via **replit.com/integrations → MCP Servers → Add MCP server**:

- **MCP Server URL:** `https://<your-app-runner-url>/mcp`

Replit detects the OAuth metadata automatically and triggers a PKCE flow. Register `https://replit.com/connectors/oauth/callback` as a redirect URI in your Okta app.

### Redirect URI summary

| Client | Redirect URI |
|---|---|
| Claude Code | `http://localhost:3000/callback` |
| Cursor native HTTP | `cursor://anysphere.cursor-mcp/oauth/callback` |
| VS Code Copilot | `http://127.0.0.1:33418/` |
| Replit | `https://replit.com/connectors/oauth/callback` |

## MCP Tools

| Tool | Description |
|---|---|
| `whoami` | Returns current user identity and group memberships from the JWT |
| `list_secrets` | List secrets, optionally filtered by name prefix |
| `get_secret` | Retrieve a secret's current value |
| `describe_secret` | Get metadata without retrieving the value |
| `create_secret` | Returns a one-time URL to enter the secret value in a browser |
| `update_secret` | Returns a one-time URL to enter the new value in a browser |
| `finalize_secret` | Commits the value after the browser form is submitted |
| `delete_secret` | Schedule or force-delete a secret |

## Access Control Tags

| Tag | Value | Effect |
|---|---|---|
| `mcp:read_groups` | `group1,group2` | Only these Okta groups can read this secret |
| `mcp:write_groups` | `group1` | Only these Okta groups can update this secret |
| `mcp:admin_groups` | `group1` | Only these Okta groups can delete this secret |

No tag = open to all authenticated users.

## Infrastructure

| Resource | Purpose |
|---|---|
| `infra/app_runner.tf` | App Runner service, IAM roles, auto-scaling (max_size=1) |
| `infra/ecr.tf` | ECR repository + automated Docker build/push on `terraform apply` |
| `infra/cognito.tf` | Optional Cognito pool for testing (`use_cognito = true`) |
| `infra/main.tf` | Provider config, auth locals |
| `infra/variables.tf` | Input variables |
| `infra/outputs.tf` | MCP endpoint, client configs, OAuth metadata |

> `max_size=1` is intentional. The secret entry token store is in-memory; multiple instances would cause form POSTs to land on the wrong instance.
