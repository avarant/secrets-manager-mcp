#!/bin/bash
# deploy.sh — Deploy the Secrets Manager MCP stack with Terraform
#
# Prerequisites:
#   - AWS CLI configured (aws configure / SSO)
#   - Terraform >= 1.6  (brew install terraform)
#   - Python 3.12+ with pip  (for Lambda build)
#
# Usage — Cognito testing (no Okta needed):
#   ./deploy.sh --use-cognito
#
# Usage — Okta production:
#   ./deploy.sh \
#     --okta-issuer   https://your-tenant.okta.com/oauth2/your-auth-server-id \
#     --okta-audience api://secrets-mcp

set -euo pipefail

USE_COGNITO=false
OKTA_ISSUER=""
OKTA_AUDIENCE=""
AWS_REGION="${AWS_REGION:-us-east-1}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --use-cognito)   USE_COGNITO=true; shift ;;
    --okta-issuer)   OKTA_ISSUER="$2"; shift 2 ;;
    --okta-audience) OKTA_AUDIENCE="$2"; shift 2 ;;
    --region)        AWS_REGION="$2"; shift 2 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

if [[ "$USE_COGNITO" == "false" && ( -z "$OKTA_ISSUER" || -z "$OKTA_AUDIENCE" ) ]]; then
  echo "Usage:"
  echo "  $0 --use-cognito"
  echo "  $0 --okta-issuer <url> --okta-audience <audience>"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/infra"

# Write terraform.tfvars
if [[ "$USE_COGNITO" == "true" ]]; then
  cat > terraform.tfvars <<EOF
aws_region  = "$AWS_REGION"
use_cognito = true
EOF
else
  cat > terraform.tfvars <<EOF
aws_region    = "$AWS_REGION"
use_cognito   = false
okta_issuer   = "$OKTA_ISSUER"
okta_audience = "$OKTA_AUDIENCE"
EOF
fi

echo "==> terraform init"
terraform init -upgrade

echo "==> terraform apply"
terraform apply -auto-approve

echo ""
echo "==> Outputs:"
terraform output

if [[ "$USE_COGNITO" == "true" ]]; then
  POOL_ID=$(terraform output -raw cognito_user_pool_id 2>/dev/null || echo "")
  if [[ -n "$POOL_ID" ]]; then
    echo ""
    echo "==> Create a test user:"
    echo "    aws cognito-idp admin-create-user \\"
    echo "      --region $AWS_REGION \\"
    echo "      --user-pool-id $POOL_ID \\"
    echo "      --username you@example.com \\"
    echo "      --temporary-password 'TempPass123!'"
  fi
fi
