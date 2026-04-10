# ── CloudWatch log group ──────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.name}"
  retention_in_days = 30
  tags              = local.tags
}

# ── IAM role ──────────────────────────────────────────────────────────────────

resource "aws_iam_role" "lambda" {
  name = "${local.name}-lambda"
  tags = local.tags

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_policy" "secrets_manager" {
  name        = "${local.name}-secrets-manager"
  description = "Least-privilege Secrets Manager access for the MCP server"
  tags        = local.tags

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "SecretsManagerAccess"
      Effect = "Allow"
      Action = [
        "secretsmanager:ListSecrets",
        "secretsmanager:DescribeSecret",
        "secretsmanager:GetSecretValue",
        "secretsmanager:CreateSecret",
        "secretsmanager:PutSecretValue",
        "secretsmanager:UpdateSecret",
        "secretsmanager:DeleteSecret",
        "secretsmanager:TagResource",
        "secretsmanager:UntagResource",
      ]
      # Scope to specific ARN prefixes once you know your naming convention.
      # e.g. "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:myapp/*"
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "secrets_manager" {
  role       = aws_iam_role.lambda.name
  policy_arn = aws_iam_policy.secrets_manager.arn
}

# ── Lambda function ───────────────────────────────────────────────────────────

resource "aws_lambda_function" "this" {
  function_name = local.name
  description   = "AWS Secrets Manager MCP server (Streamable HTTP + OAuth 2.1 PKCE)"
  role          = aws_iam_role.lambda.arn

  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  handler          = "main.handler"
  runtime          = "python3.12"

  timeout     = 30
  memory_size = 256

  environment {
    variables = {
      OAUTH_ISSUER                 = local.oauth_issuer
      OAUTH_CLIENT_ID              = local.oauth_client_id
      OAUTH_AUTHORIZATION_ENDPOINT = local.oauth_authorization_endpoint
      OAUTH_TOKEN_ENDPOINT         = local.oauth_token_endpoint
      OAUTH_JWKS_URI               = local.oauth_jwks_uri
      LOG_LEVEL                    = "INFO"
    }
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.lambda.name
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_basic,
    aws_cloudwatch_log_group.lambda,
  ]

  tags = local.tags
}
