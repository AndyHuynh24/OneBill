#!/usr/bin/env bash
# Writes frontend/.env.local from the deployed stacks' CloudFormation outputs.
# Usage: write_frontend_env.sh [OneBill | OneBill-Staging]   (default: OneBill, production)
# These are public identifiers (pool ID, client ID, URLs), not secrets; the file is gitignored
# only so account-specific values stay out of the repo.
set -euo pipefail

REGION="us-east-1"
PREFIX="${1:-OneBill}"
OUT_FILE="$(cd "$(dirname "$0")/.." && pwd)/frontend/.env.local"

output() {
  aws cloudformation describe-stacks --region "$REGION" --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text
}

cat > "$OUT_FILE" <<EOF
VITE_USER_POOL_ID=$(output "$PREFIX-Auth" UserPoolId)
VITE_USER_POOL_CLIENT_ID=$(output "$PREFIX-Auth" UserPoolClientId)
VITE_COGNITO_DOMAIN=$(output "$PREFIX-Auth" HostedDomain)
VITE_API_URL=$(output "$PREFIX-Api" ApiUrl)
EOF

echo "Wrote $OUT_FILE for $PREFIX"
