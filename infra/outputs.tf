output "mcp_endpoint" {
  description = "MCP server URL — use this in your .mcp.json / mcp.json"
  value       = "${trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")}/mcp"
}

output "oauth_metadata_url" {
  description = "RFC 9728 protected resource metadata — MCP clients auto-discover this"
  value       = "${trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")}/.well-known/oauth-protected-resource"
}

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
  description = "Cognito User Pool ID. Create test users: aws cognito-idp admin-create-user --user-pool-id <id> --username user@example.com"
  value       = local.cognito_pool_id
}

output "cognito_client_id" {
  description = "Cognito App Client ID — use this as client_id in cursor-mcp.json"
  value       = local.cognito_client_id
}

output "cognito_hosted_ui" {
  description = "Cognito hosted UI base URL (authorization endpoint)"
  value       = local.cognito_hosted_ui
}

output "lambda_function_name" {
  value = aws_lambda_function.this.function_name
}
