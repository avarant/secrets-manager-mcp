variable "aws_region" {
  type        = string
  default     = "us-east-1"
  description = "AWS region to deploy into."
}

variable "docker_platform" {
  type        = string
  default     = "linux/amd64"
  description = "Container platform to build for App Runner. Use linux/amd64 unless you have explicitly configured a compatible runtime architecture."
}

# ── Auth provider ─────────────────────────────────────────────────────────────

variable "use_cognito" {
  type        = bool
  default     = false
  description = "Create a Cognito User Pool for testing. Set to false when switching to Okta."
}

variable "okta_issuer" {
  type        = string
  default     = ""
  description = "Okta authorization server issuer URL. Required when use_cognito=false. e.g. https://your-tenant.okta.com/oauth2/aus1xxxxxxxxx"
}

variable "okta_audience" {
  type        = string
  default     = ""
  description = "Expected audience in Okta access tokens. Required when use_cognito=false. e.g. api://secrets-mcp"
}

variable "okta_client_id" {
  type        = string
  default     = ""
  description = "Okta app client ID returned by the static DCR endpoint. Required when use_cognito=false."
}

# ── Cognito (only used when use_cognito=true) ─────────────────────────────────

variable "cognito_domain_prefix" {
  type        = string
  default     = ""
  description = "Prefix for the Cognito hosted UI domain (must be globally unique). Defaults to 'secrets-mcp-<last6ofAccountId>'."
}
