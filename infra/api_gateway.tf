# ── HTTP API ──────────────────────────────────────────────────────────────────

resource "aws_apigatewayv2_api" "this" {
  name          = local.name
  protocol_type = "HTTP"
  description   = "Secrets Manager MCP — OAuth 2.1 PKCE"
  tags          = local.tags

  cors_configuration {
    allow_headers = [
      "Authorization",
      "Content-Type",
      "Mcp-Session-Id",  # Streamable HTTP session header
      "Last-Event-ID",   # SSE resume header
    ]
    allow_methods = ["*"]
    allow_origins = ["*"]
    max_age       = 3600
  }
}

# ── $default stage with auto-deploy ──────────────────────────────────────────

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/apigateway/${local.name}"
  retention_in_days = 30
  tags              = local.tags
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true
  tags        = local.tags

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      requestTime    = "$context.requestTime"
      httpMethod     = "$context.httpMethod"
      routeKey       = "$context.routeKey"
      status         = "$context.status"
      responseLength = "$context.responseLength"
      errorMessage   = "$context.error.message"
    })
  }
}

# ── Lambda integration ────────────────────────────────────────────────────────

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.this.invoke_arn
  payload_format_version = "2.0"
}

# Allow API Gateway to invoke the Lambda
resource "aws_lambda_permission" "api_gw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.this.execution_arn}/*/*"
}

# ── JWT Authorizer (Okta or Cognito) ─────────────────────────────────────────

resource "aws_apigatewayv2_authorizer" "jwt" {
  api_id           = aws_apigatewayv2_api.this.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "OAuthJWT"

  jwt_configuration {
    issuer   = local.oauth_issuer
    audience = [local.oauth_audience]
  }
}

# ── Routes ────────────────────────────────────────────────────────────────────

# Protected: MCP endpoint — requires valid JWT
resource "aws_apigatewayv2_route" "mcp" {
  api_id             = aws_apigatewayv2_api.this.id
  route_key          = "ANY /mcp"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.jwt.id
}

# Public: RFC 9728 — points clients to our auth server metadata
resource "aws_apigatewayv2_route" "oauth_protected_resource" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "GET /.well-known/oauth-protected-resource"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

# Public: RFC 8414 — auth server metadata with our /register endpoint
resource "aws_apigatewayv2_route" "oauth_authorization_server" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "GET /.well-known/oauth-authorization-server"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

# Public: RFC 7591 static DCR — returns pre-registered client_id
resource "aws_apigatewayv2_route" "register" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "POST /register"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

# Public: health check
resource "aws_apigatewayv2_route" "health" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "GET /health"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}
