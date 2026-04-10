terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

# ── Auth locals ───────────────────────────────────────────────────────────────
# Resolves the correct issuer/audience whether we're using Cognito or Okta.
# When use_cognito=false, the Cognito resources have count=0 so we fall back
# to the provided okta_issuer / okta_audience variables.

locals {
  name = "secrets-mcp"

  tags = {
    Project   = "secrets-mcp"
    ManagedBy = "terraform"
  }

  cognito_pool_id   = length(aws_cognito_user_pool.this) > 0 ? aws_cognito_user_pool.this[0].id : ""
  cognito_client_id = length(aws_cognito_user_pool_client.this) > 0 ? aws_cognito_user_pool_client.this[0].id : ""
  cognito_domain    = length(aws_cognito_user_pool_domain.this) > 0 ? aws_cognito_user_pool_domain.this[0].domain : ""
  cognito_hosted_ui = local.cognito_domain != "" ? "https://${local.cognito_domain}.auth.${var.aws_region}.amazoncognito.com" : ""

  oauth_issuer   = var.use_cognito ? "https://cognito-idp.${var.aws_region}.amazonaws.com/${local.cognito_pool_id}" : var.okta_issuer
  oauth_audience = var.use_cognito ? local.cognito_client_id : var.okta_audience

  # Endpoints passed to Lambda for the DCR facade
  oauth_client_id              = var.use_cognito ? local.cognito_client_id : var.okta_client_id
  oauth_authorization_endpoint = var.use_cognito ? "${local.cognito_hosted_ui}/oauth2/authorize" : "${var.okta_issuer}/v1/authorize"
  oauth_token_endpoint         = var.use_cognito ? "${local.cognito_hosted_ui}/oauth2/token"     : "${var.okta_issuer}/v1/token"
  oauth_jwks_uri               = var.use_cognito ? "https://cognito-idp.${var.aws_region}.amazonaws.com/${local.cognito_pool_id}/.well-known/jwks.json" : "${var.okta_issuer}/v1/keys"
}

# ── Lambda package build ──────────────────────────────────────────────────────
# Installs Python deps for linux/x86_64 and zips with the server source.
# Re-runs only when requirements.txt or main.py changes.

resource "null_resource" "lambda_build" {
  triggers = {
    requirements = filemd5("${path.module}/../server/requirements-lambda.txt")
    source       = filemd5("${path.module}/../server/main.py")
  }

  provisioner "local-exec" {
    working_dir = path.module
    command     = <<-SHELL
      set -e
      rm -rf .build && mkdir -p .build
      pip install \
        -r ../server/requirements-lambda.txt \
        -t .build \
        --platform manylinux2014_x86_64 \
        --implementation cp \
        --python-version 312 \
        --only-binary=:all: \
        --quiet
      cp ../server/main.py .build/
    SHELL
  }
}

data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/.build"
  output_path = "${path.module}/.build/lambda.zip"
  depends_on  = [null_resource.lambda_build]
}
