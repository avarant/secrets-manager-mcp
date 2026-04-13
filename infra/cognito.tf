# Cognito resources are only created when var.use_cognito = true.
# To enable: set use_cognito = true in terraform.tfvars (no Okta values needed).
# To switch to Okta later: set use_cognito = false + okta_issuer + okta_audience,
# then re-run terraform apply.  The User Pool is retained on destroy (RemovalPolicy).

locals {
  # Use last 6 digits of the account ID to make the domain prefix unique by default
  cognito_domain_prefix = var.cognito_domain_prefix != "" ? var.cognito_domain_prefix : (
    "secrets-mcp-${substr(data.aws_caller_identity.current.account_id, -6, 6)}"
  )
}

# ── User Pool ─────────────────────────────────────────────────────────────────

resource "aws_cognito_user_pool" "this" {
  count = var.use_cognito ? 1 : 0

  name = local.name
  tags = local.tags

  # No self sign-up — admin creates test users via CLI or console
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length                   = 12
    require_uppercase                = true
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

# ── Resource server (custom scopes) ──────────────────────────────────────────

resource "aws_cognito_resource_server" "this" {
  count = var.use_cognito ? 1 : 0

  identifier   = "secrets-mcp"
  name         = "Secrets MCP API"
  user_pool_id = aws_cognito_user_pool.this[0].id

  scope {
    scope_name        = "read"
    scope_description = "Read, list, and describe secrets"
  }

  scope {
    scope_name        = "write"
    scope_description = "Create, update, and delete secrets"
  }
}

# ── App Client (PKCE, no client secret) ──────────────────────────────────────

resource "aws_cognito_user_pool_client" "this" {
  count = var.use_cognito ? 1 : 0

  name         = "${local.name}-client"
  user_pool_id = aws_cognito_user_pool.this[0].id

  generate_secret = false # Public client — PKCE replaces the secret

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes = [
    "openid",
    "profile",
    "email",
    "secrets-mcp/read",
    "secrets-mcp/write",
  ]

  callback_urls = [
    "http://localhost:3334/oauth/callback", # mcp-remote (--callback-port 3334)
    "http://localhost:3334/callback",
    "http://localhost:3000/oauth/callback", # Claude Code (callbackPort: 3000)
    "http://localhost:3000/callback",
  ]

  supported_identity_providers = ["COGNITO"]

  # Token lifetimes
  access_token_validity  = 60  # minutes
  id_token_validity      = 60  # minutes
  refresh_token_validity = 8   # hours (default unit)

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "hours"
  }

  enable_token_revocation       = true
  prevent_user_existence_errors = "ENABLED"

  depends_on = [aws_cognito_resource_server.this]
}

# ── Hosted UI domain ──────────────────────────────────────────────────────────

resource "aws_cognito_user_pool_domain" "this" {
  count = var.use_cognito ? 1 : 0

  domain       = local.cognito_domain_prefix
  user_pool_id = aws_cognito_user_pool.this[0].id
}
