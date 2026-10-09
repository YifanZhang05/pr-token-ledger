#!/usr/bin/env bash
# Deploy PR Token Ledger to the Hackweek sandbox A (Lambda + internal ALB + private HTTPS DNS).
# Usage: bash deploy/deploy.sh          # create everything (safe to re-run; skips what exists)
#        bash deploy/deploy.sh update   # re-upload app code only
# Every created ID is appended to deploy/state.env so a partial run can be resumed or cleaned up.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_ROLE_ARN AWS_WEB_IDENTITY_TOKEN_FILE AWS_ROLE_SESSION_NAME AWS_ENDPOINT_URL AWS_ENDPOINT_URL_STS
export AWS_PROFILE=hackweek-a AWS_REGION=us-west-2 AWS_DEFAULT_REGION=us-west-2 AWS_STS_REGIONAL_ENDPOINTS=regional AWS_PAGER=""
ACCOUNT=632472162753
PERSON=yifan-zhang
PROJECT=pr-token-ledger
OWNER_EMAIL=yifan.zhang@kikoff.com
PREFIX="hackweek-${PERSON}-${PROJECT}"          # 35 chars; ALB/TG names must be <=32 so they use SHORT
SHORT="hackweek-${PERSON}-ledger"
VPC=vpc-09f7e657a49f12dbc
SUBNETS="subnet-0f12bcf8bb29245a6 subnet-096bef3bec563ef8d"
SG=sg-0be56bdc1d1df9d0e
BOUNDARY="arn:aws:iam::${ACCOUNT}:policy/hackweek-permissions-boundary"
HTTPS_DOMAIN=a.hackweek.kikoff.dev
HOSTNAME="${PERSON}-${PROJECT}.${HTTPS_DOMAIN}"
BUCKET="${PREFIX}-data"
FUNCTION="${PREFIX}"
ROLE="${PREFIX}-lambda"
LOG_GROUP="/aws/lambda/${FUNCTION}"
ADMIN_PARAM="/hackweek/${PERSON}/${PROJECT}/admin-token"
SESSION_SECRET_PARAM="/hackweek/${PERSON}/${PROJECT}/session-secret"
GITHUB_PARAM="/hackweek/${PERSON}/${PROJECT}/github-oauth"
TAGS_KV="project=${PROJECT},owner=${OWNER_EMAIL},created-by=${OWNER_EMAIL}"
TAGS_JSON="[{\"Key\":\"project\",\"Value\":\"${PROJECT}\"},{\"Key\":\"owner\",\"Value\":\"${OWNER_EMAIL}\"},{\"Key\":\"created-by\",\"Value\":\"${OWNER_EMAIL}\"}]"
STATE=deploy/state.env
touch "$STATE"; # shellcheck disable=SC1090
source "$STATE"
record() { grep -q "^$1=" "$STATE" && sed -i '' "s|^$1=.*|$1=$2|" "$STATE" || echo "$1=$2" >> "$STATE"; export "$1=$2"; echo "  recorded $1=$2"; }

identity=$(aws sts get-caller-identity --query '[Account,Arn]' --output text)
read -r acct arn <<<"$identity"
[[ "$acct" == "$ACCOUNT" && "$arn" == *"assumed-role/AWSReservedSSO_hackweek-administrator_"* ]] || { echo "Wrong identity: $identity" >&2; exit 1; }
echo "Deploying as $arn"

build_zip() {
  rm -f deploy/app.zip
  (cd app && zip -q -r ../deploy/app.zip handler.py)
  echo "  built deploy/app.zip ($(du -h deploy/app.zip | cut -f1))"
}

if [[ "${1:-}" == "update" ]]; then
  build_zip
  aws lambda update-function-code --function-name "$FUNCTION" --zip-file fileb://deploy/app.zip --query 'LastUpdateStatus' --output text
  aws lambda wait function-updated --function-name "$FUNCTION"
  echo "Updated. Test: curl --fail https://${HOSTNAME}/healthz"
  exit 0
fi

echo "1/9 S3 bucket $BUCKET"
if ! aws s3api head-bucket --bucket "$BUCKET" --endpoint-url https://s3.us-west-2.amazonaws.com 2>/dev/null; then
  aws s3api create-bucket --bucket "$BUCKET" --endpoint-url https://s3.us-west-2.amazonaws.com \
    --create-bucket-configuration LocationConstraint=us-west-2 >/dev/null
fi
aws s3api put-public-access-block --bucket "$BUCKET" --endpoint-url https://s3.us-west-2.amazonaws.com \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-tagging --bucket "$BUCKET" --endpoint-url https://s3.us-west-2.amazonaws.com --tagging "{\"TagSet\":${TAGS_JSON}}"
record BUCKET "$BUCKET"

echo "2/9 admin token in SSM $ADMIN_PARAM"
if ! aws ssm get-parameter --name "$ADMIN_PARAM" >/dev/null 2>&1; then
  aws ssm put-parameter --name "$ADMIN_PARAM" --type SecureString --value "$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" \
    --tags "$TAGS_JSON" >/dev/null
fi
record ADMIN_PARAM "$ADMIN_PARAM"
if ! aws ssm get-parameter --name "$SESSION_SECRET_PARAM" >/dev/null 2>&1; then
  aws ssm put-parameter --name "$SESSION_SECRET_PARAM" --type SecureString --value "$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')" \
    --tags "$TAGS_JSON" >/dev/null
fi
record SESSION_SECRET_PARAM "$SESSION_SECRET_PARAM"
record GITHUB_PARAM "$GITHUB_PARAM"   # written by deploy/set-github.sh

echo "3/9 IAM role $ROLE (with permissions boundary)"
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --permissions-boundary "$BOUNDARY" --tags "$TAGS_JSON" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
fi
aws iam put-role-policy --role-name "$ROLE" --policy-name app --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[
  {\"Effect\":\"Allow\",\"Action\":[\"logs:CreateLogStream\",\"logs:PutLogEvents\"],\"Resource\":\"arn:aws:logs:us-west-2:${ACCOUNT}:log-group:${LOG_GROUP}:*\"},
  {\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\",\"s3:PutObject\"],\"Resource\":\"arn:aws:s3:::${BUCKET}/*\"},
  {\"Effect\":\"Allow\",\"Action\":[\"s3:ListBucket\"],\"Resource\":\"arn:aws:s3:::${BUCKET}\"},
  {\"Effect\":\"Allow\",\"Action\":[\"ssm:GetParameter\"],\"Resource\":[\"arn:aws:ssm:us-west-2:${ACCOUNT}:parameter${ADMIN_PARAM}\",\"arn:aws:ssm:us-west-2:${ACCOUNT}:parameter${SESSION_SECRET_PARAM}\",\"arn:aws:ssm:us-west-2:${ACCOUNT}:parameter${GITHUB_PARAM}\"]}]}"
ROLE_ARN=$(aws iam get-role --role-name "$ROLE" --query Role.Arn --output text)
record ROLE_ARN "$ROLE_ARN"

echo "4/9 log group $LOG_GROUP"
aws logs create-log-group --log-group-name "$LOG_GROUP" --tags "$TAGS_KV" 2>/dev/null || true
aws logs put-retention-policy --log-group-name "$LOG_GROUP" --retention-in-days 7
record LOG_GROUP "$LOG_GROUP"

echo "5/9 Lambda function $FUNCTION"
build_zip
ENV_VARS="Variables={LEDGER_BUCKET=${BUCKET},LEDGER_DB_KEY=ledger.db,ADMIN_TOKEN_PARAM=${ADMIN_PARAM},SESSION_SECRET_PARAM=${SESSION_SECRET_PARAM},GITHUB_PARAM=${GITHUB_PARAM}}"
if ! aws lambda get-function --function-name "$FUNCTION" >/dev/null 2>&1; then
  for i in 1 2 3 4 5 6; do  # IAM role propagation can take a few seconds
    if aws lambda create-function --function-name "$FUNCTION" --runtime python3.13 --architectures arm64 \
        --role "$ROLE_ARN" --handler handler.handler --zip-file fileb://deploy/app.zip --timeout 30 --memory-size 512 \
        --environment "$ENV_VARS" --tags "$TAGS_KV" >/dev/null 2>deploy/.err; then break; fi
    grep -q "role" deploy/.err && [ $i -lt 6 ] && { sleep 5; continue; }; cat deploy/.err >&2; exit 1
  done
else
  aws lambda update-function-code --function-name "$FUNCTION" --zip-file fileb://deploy/app.zip >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION"
  aws lambda update-function-configuration --function-name "$FUNCTION" --environment "$ENV_VARS" --timeout 30 --memory-size 512 >/dev/null
fi
aws lambda wait function-active-v2 --function-name "$FUNCTION"
aws lambda wait function-updated-v2 --function-name "$FUNCTION"
aws lambda put-function-concurrency --function-name "$FUNCTION" --reserved-concurrent-executions 1 >/dev/null
FUNCTION_ARN=$(aws lambda get-function --function-name "$FUNCTION" --query Configuration.FunctionArn --output text)
record FUNCTION_ARN "$FUNCTION_ARN"

echo "6/9 target group ${SHORT}-tg"
TG_ARN=$(aws elbv2 describe-target-groups --names "${SHORT}-tg" --query 'TargetGroups[0].TargetGroupArn' --output text 2>/dev/null || true)
if [[ -z "$TG_ARN" || "$TG_ARN" == "None" ]]; then
  TG_ARN=$(aws elbv2 create-target-group --name "${SHORT}-tg" --target-type lambda --tags "$TAGS_JSON" --query 'TargetGroups[0].TargetGroupArn' --output text)
fi
record TG_ARN "$TG_ARN"
aws lambda add-permission --function-name "$FUNCTION" --statement-id alb-invoke --action lambda:InvokeFunction \
  --principal elasticloadbalancing.amazonaws.com --source-arn "$TG_ARN" --source-account "$ACCOUNT" >/dev/null 2>&1 || true
aws elbv2 register-targets --target-group-arn "$TG_ARN" --targets "Id=${FUNCTION_ARN}"

echo "7/9 internal ALB $SHORT"
ALB_ARN=$(aws elbv2 describe-load-balancers --names "$SHORT" --query 'LoadBalancers[0].LoadBalancerArn' --output text 2>/dev/null || true)
if [[ -z "$ALB_ARN" || "$ALB_ARN" == "None" ]]; then
  # shellcheck disable=SC2086
  ALB_ARN=$(aws elbv2 create-load-balancer --name "$SHORT" --scheme internal --type application --ip-address-type ipv4 \
    --subnets $SUBNETS --security-groups "$SG" --tags "$TAGS_JSON" --query 'LoadBalancers[0].LoadBalancerArn' --output text)
fi
record ALB_ARN "$ALB_ARN"
aws elbv2 wait load-balancer-available --load-balancer-arns "$ALB_ARN"
read -r ALB_DNS ALB_ZONE ALB_SCHEME <<<"$(aws elbv2 describe-load-balancers --load-balancer-arns "$ALB_ARN" --query 'LoadBalancers[0].[DNSName,CanonicalHostedZoneId,Scheme]' --output text)"
[[ "$ALB_SCHEME" == "internal" ]] || { echo "ALB is not internal!" >&2; exit 1; }
record ALB_DNS "$ALB_DNS"

echo "8/9 HTTPS listener"
CERT_ARN=$(aws acm list-certificates --query "CertificateSummaryList[?DomainName=='*.${HTTPS_DOMAIN}' && Status=='ISSUED'].CertificateArn | [0]" --output text)
[[ -n "$CERT_ARN" && "$CERT_ARN" != "None" ]] || { echo "Shared certificate missing; ask Infra." >&2; exit 1; }
LISTENER_ARN=$(aws elbv2 describe-listeners --load-balancer-arn "$ALB_ARN" --query "Listeners[?Port==\`443\`].ListenerArn | [0]" --output text 2>/dev/null || true)
if [[ -z "$LISTENER_ARN" || "$LISTENER_ARN" == "None" ]]; then
  LISTENER_ARN=$(aws elbv2 create-listener --load-balancer-arn "$ALB_ARN" --protocol HTTPS --port 443 \
    --ssl-policy ELBSecurityPolicy-TLS13-1-2-2021-06 --certificates "CertificateArn=${CERT_ARN}" \
    --default-actions "Type=forward,TargetGroupArn=${TG_ARN}" --tags "$TAGS_JSON" --query 'Listeners[0].ListenerArn' --output text)
fi
record LISTENER_ARN "$LISTENER_ARN"

echo "9/9 private DNS $HOSTNAME"
ZONE_ID=$(aws route53 list-hosted-zones-by-name --dns-name "$HTTPS_DOMAIN" \
  --query "HostedZones[?Name=='${HTTPS_DOMAIN}.' && Config.PrivateZone].Id | [0]" --output text)
[[ -n "$ZONE_ID" && "$ZONE_ID" != "None" ]] || { echo "Shared private zone missing; ask Infra." >&2; exit 1; }
CHANGE_ID=$(aws route53 change-resource-record-sets --hosted-zone-id "$ZONE_ID" --change-batch "{\"Changes\":[{\"Action\":\"UPSERT\",\"ResourceRecordSet\":{
  \"Name\":\"${HOSTNAME}.\",\"Type\":\"A\",\"AliasTarget\":{\"HostedZoneId\":\"${ALB_ZONE}\",\"DNSName\":\"${ALB_DNS}\",\"EvaluateTargetHealth\":false}}}]}" \
  --query 'ChangeInfo.Id' --output text)
record ZONE_ID "$ZONE_ID"
record HOSTNAME "$HOSTNAME"
record URL "https://${HOSTNAME}"
aws route53 wait resource-record-sets-changed --id "$CHANGE_ID"
echo "DNS INSYNC."

echo "Direct Lambda invoke test:"
aws lambda invoke --function-name "$FUNCTION" --payload '{"httpMethod":"GET","path":"/healthz","headers":{}}' --cli-binary-format raw-in-base64-out deploy/.out.json >/dev/null && cat deploy/.out.json; echo
rm -f deploy/.out.json deploy/.err
echo "Done. Verify through Twingate:  curl --noproxy '*' --fail --show-error --max-time 20 https://${HOSTNAME}/healthz"
