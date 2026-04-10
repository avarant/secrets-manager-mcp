# Okta Setup (one-time)

## 1. Create a Native Application

Applications → Create App Integration → OIDC → **Native Application**

| Field | Value |
|---|---|
| App name | Secrets Manager MCP *(or your preference)* |
| Grant type | Authorization Code |
| **Client authentication** | **None** (PKCE — no client secret) |
| Sign-in redirect URI | `http://localhost:3000/callback` |
| Sign-in redirect URI | `cursor://anysphere.cursor-mcp/oauth/callback` |
| Sign-out redirect URI | `http://localhost:3000` |
| Controlled access | Assign to relevant group |

> **Why Native, not Web?** MCP clients (Claude Code, Cursor) run on the user's
> machine and cannot safely store a client secret. Native apps use PKCE instead —
> a one-time random challenge generated per login. No secret is ever stored.

Copy the **Client ID** — it looks like `0oa1xxxxxxxxx`.

## 2. Use the Default Authorization Server

No custom authorization server needed. Use `/oauth2/default`.

| Value | What to use |
|---|---|
| Issuer | `https://your-org.okta.com/oauth2/default` |
| Audience | `api://default` |
| Scopes | `openid profile email` (standard — no custom scopes needed) |

## 3. Deploy with Terraform

```hcl
# infra/terraform.tfvars
aws_region     = "us-east-2"
use_cognito    = false
okta_issuer    = "https://your-org.okta.com/oauth2/default"
okta_client_id = "0oa1xxxxxxxxx"
okta_audience  = "api://default"
```

```bash
cd infra
terraform init
terraform apply
```

## 4. Client Configuration

**Claude Code** — copy `client-configs/claude-code.json` to `.mcp.json` in your project,
replacing `YOUR_API_ID` and `YOUR_REGION` with the `mcp_endpoint` Terraform output.

**Cursor** — copy `client-configs/cursor-mcp.json` to `~/.cursor/mcp.json`,
replacing the placeholder URL.

## Testing with Cognito (no Okta app yet)

```hcl
# infra/terraform.tfvars
aws_region  = "us-east-2"
use_cognito = true
```

Create a test user after deploy:
```bash
aws cognito-idp admin-create-user \
  --user-pool-id <cognito_user_pool_id output> \
  --username user@example.com \
  --temporary-password "TempPass123!" \
  --region us-east-2

aws cognito-idp admin-set-user-password \
  --user-pool-id <cognito_user_pool_id output> \
  --username user@example.com \
  --password "PermanentPass1!" \
  --permanent \
  --region us-east-2
```
