locals {
  app_runner_url = "https://${aws_apprunner_service.this.service_url}"
  mcp_endpoint   = "${local.app_runner_url}/mcp"
}

output "mcp_endpoint" {
  description = "MCP server URL — use this in your .mcp.json / mcp.json"
  value       = local.mcp_endpoint
}

output "app_runner_url" {
  description = "App Runner base URL"
  value       = local.app_runner_url
}

# ── Ready-to-use client configs ───────────────────────────────────────────────

output "claude_code_mcp_json" {
  description = "Paste into your project .mcp.json or ~/.claude.json mcpServers"
  value = jsonencode({
    mcpServers = {
      secrets-manager = {
        type = "http"
        url  = local.mcp_endpoint
        auth = {
          type = "oauth"
          pkce = true
        }
      }
    }
  })
}

output "cursor_mcp_json" {
  description = "Paste into ~/.cursor/mcp.json"
  value = jsonencode({
    mcpServers = {
      secrets-manager = {
        command = "npx"
        args = [
          "mcp-remote@0.1.38",
          local.mcp_endpoint,
        ]
      }
    }
  })
}

# ── OAuth metadata ────────────────────────────────────────────────────────────

output "oauth_issuer" {
  description = "Active OAuth 2.0 authorization server issuer URL"
  value       = local.oauth_issuer
}

output "oauth_audience" {
  description = "Expected audience in access tokens"
  value       = local.oauth_audience
}

# ── Cognito-specific outputs (only populated when use_cognito=true) ───────────

output "cognito_user_pool_id" {
  description = "Cognito User Pool ID"
  value       = local.cognito_pool_id
}

output "cognito_client_id" {
  description = "Cognito App Client ID"
  value       = local.cognito_client_id
}

output "cognito_hosted_ui" {
  description = "Cognito hosted UI base URL"
  value       = local.cognito_hosted_ui
}
