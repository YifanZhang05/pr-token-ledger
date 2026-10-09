#!/usr/bin/env bash
# Store the GitHub OAuth app credentials so the site and CLI use "Sign in with GitHub".
# Usage: bash deploy/set-github.sh <client_id> <client_secret>
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[ "$#" -eq 2 ] || { echo "usage: bash deploy/set-github.sh <client_id> <client_secret>" >&2; exit 1; }
export AWS_PROFILE=hackweek-a AWS_REGION=us-west-2 AWS_DEFAULT_REGION=us-west-2 AWS_PAGER=""
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
# shellcheck disable=SC1091
source deploy/state.env
value=$(python3 -c 'import json,sys; print(json.dumps({"client_id": sys.argv[1], "client_secret": sys.argv[2]}))' "$1" "$2")
if aws ssm get-parameter --name "$GITHUB_PARAM" >/dev/null 2>&1; then
  aws ssm put-parameter --name "$GITHUB_PARAM" --type SecureString --value "$value" --overwrite >/dev/null
else
  aws ssm put-parameter --name "$GITHUB_PARAM" --type SecureString --value "$value" \
    --tags '[{"Key":"project","Value":"pr-token-ledger"},{"Key":"owner","Value":"yifan.zhang@kikoff.com"},{"Key":"created-by","Value":"yifan.zhang@kikoff.com"}]' >/dev/null
fi
# Restart containers so the new settings are read.
aws lambda update-function-configuration --function-name hackweek-yifan-zhang-pr-token-ledger \
  --environment "Variables={LEDGER_BUCKET=${BUCKET},LEDGER_DB_KEY=ledger.db,ADMIN_TOKEN_PARAM=${ADMIN_PARAM},SESSION_SECRET_PARAM=${SESSION_SECRET_PARAM},GITHUB_PARAM=${GITHUB_PARAM},CONFIG_SET_AT=$(date +%s)}" >/dev/null
aws lambda wait function-updated --function-name hackweek-yifan-zhang-pr-token-ledger
echo "GitHub sign-in configured. Test: open ${URL}/login"
