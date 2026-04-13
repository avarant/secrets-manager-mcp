# ── IAM: Secrets Manager access policy ───────────────────────────────────────

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
      Resource = "*"
    }]
  })
}

# ── IAM: ECR image pull role (used by App Runner build service) ───────────────

resource "aws_iam_role" "apprunner_access" {
  name = "${local.name}-apprunner-access"
  tags = local.tags

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "build.apprunner.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "apprunner_ecr" {
  role       = aws_iam_role.apprunner_access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

# ── IAM: instance role (used by the running container) ────────────────────────

resource "aws_iam_role" "apprunner_instance" {
  name = "${local.name}-apprunner-instance"
  tags = local.tags

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "tasks.apprunner.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "apprunner_secrets_manager" {
  role       = aws_iam_role.apprunner_instance.name
  policy_arn = aws_iam_policy.secrets_manager.arn
}

# ── App Runner auto-scaling (single instance — in-memory futures require it) ──

resource "aws_apprunner_auto_scaling_configuration_version" "single" {
  auto_scaling_configuration_name = "${local.name}-single"
  max_concurrency                  = 100
  min_size                         = 1
  max_size                         = 1
  tags                             = local.tags
}

# ── App Runner service ────────────────────────────────────────────────────────

resource "aws_apprunner_service" "this" {
  service_name                       = local.name
  auto_scaling_configuration_arn     = aws_apprunner_auto_scaling_configuration_version.single.arn
  tags                               = local.tags

  source_configuration {
    authentication_configuration {
      access_role_arn = aws_iam_role.apprunner_access.arn
    }
    image_repository {
      image_identifier      = "${aws_ecr_repository.this.repository_url}:latest"
      image_repository_type = "ECR"
      image_configuration {
        port = "8000"
        runtime_environment_variables = {
          OAUTH_ISSUER                 = local.oauth_issuer
          OAUTH_CLIENT_ID              = local.oauth_client_id
          OAUTH_AUTHORIZATION_ENDPOINT = local.oauth_authorization_endpoint
          OAUTH_TOKEN_ENDPOINT         = local.oauth_token_endpoint
          OAUTH_JWKS_URI               = local.oauth_jwks_uri
          OAUTH_AUDIENCE               = local.oauth_audience
          LOG_LEVEL                    = "INFO"
        }
      }
    }
    auto_deployments_enabled = false
  }

  instance_configuration {
    instance_role_arn = aws_iam_role.apprunner_instance.arn
    cpu               = "256"
    memory            = "512"
  }

  health_check_configuration {
    protocol            = "HTTP"
    path                = "/health"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 1
    unhealthy_threshold = 5
  }

  depends_on = [
    aws_iam_role_policy_attachment.apprunner_ecr,
    aws_iam_role_policy_attachment.apprunner_secrets_manager,
    null_resource.docker_build_push,
  ]
}
