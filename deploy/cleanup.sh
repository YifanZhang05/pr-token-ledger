#!/usr/bin/env bash
# Remove ONLY this project's resources (recorded in deploy/state.env). Shared zone/cert/VPC/SG are never touched.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export AWS_PROFILE=hackweek-a AWS_REGION=us-west-2 AWS_DEFAULT_REGION=us-west-2 AWS_PAGER=""
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
# shellcheck disable=SC1091
source deploy/state.env
FUNCTION=hackweek-yifan-zhang-pr-token-ledger
ROLE=${FUNCTION}-lambda
echo "This deletes the PR Token Ledger deployment (project=pr-token-ledger, owner=yifan.zhang@kikoff.com)."
[[ "${1:-}" == "--yes" ]] || { echo "Re-run with --yes to proceed."; exit 1; }
set -x
aws route53 change-resource-record-sets --hosted-zone-id "$ZONE_ID" --change-batch "{\"Changes\":[{\"Action\":\"DELETE\",\"ResourceRecordSet\":{\"Name\":\"${HOSTNAME}.\",\"Type\":\"A\",\"AliasTarget\":{\"HostedZoneId\":\"$(aws elbv2 describe-load-balancers --load-balancer-arns "$ALB_ARN" --query 'LoadBalancers[0].CanonicalHostedZoneId' --output text)\",\"DNSName\":\"${ALB_DNS}\",\"EvaluateTargetHealth\":false}}}]}"
aws elbv2 delete-listener --listener-arn "$LISTENER_ARN"
aws elbv2 delete-load-balancer --load-balancer-arn "$ALB_ARN"
sleep 20
aws elbv2 delete-target-group --target-group-arn "$TG_ARN"
aws lambda delete-function --function-name "$FUNCTION"
aws logs delete-log-group --log-group-name "$LOG_GROUP"
aws iam delete-role-policy --role-name "$ROLE" --policy-name app
aws iam delete-role --role-name "$ROLE"
aws ssm delete-parameter --name "$ADMIN_PARAM"
aws ssm delete-parameter --name "$SESSION_SECRET_PARAM"
aws ssm delete-parameter --name "$GITHUB_PARAM" 2>/dev/null || true
aws s3 rm "s3://${BUCKET}" --recursive --endpoint-url https://s3.us-west-2.amazonaws.com
aws s3api delete-bucket --bucket "$BUCKET" --endpoint-url https://s3.us-west-2.amazonaws.com
set +x
echo "Cleanup complete. Shared foundation (zone, certificate, VPC, SG) untouched."
