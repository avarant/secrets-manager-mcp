terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
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

  oauth_client_id              = var.use_cognito ? local.cognito_client_id : var.okta_client_id
  oauth_authorization_endpoint = var.use_cognito ? "${local.cognito_hosted_ui}/oauth2/authorize" : "${var.okta_issuer}/v1/authorize"
  oauth_token_endpoint         = var.use_cognito ? "${local.cognito_hosted_ui}/oauth2/token"     : "${var.okta_issuer}/v1/token"
  oauth_jwks_uri               = var.use_cognito ? "https://cognito-idp.${var.aws_region}.amazonaws.com/${local.cognito_pool_id}/.well-known/jwks.json" : "${var.okta_issuer}/v1/keys"
}
