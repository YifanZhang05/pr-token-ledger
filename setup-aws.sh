#!/usr/bin/env bash
# Participant SSO setup only; does not create AWS resources.
set -euo pipefail

profile=hackweek-a
config_file="${AWS_CONFIG_FILE:-$HOME/.aws/config}"
prepare_only=false
skip_login=false
device_code=false
while [ "$#" -gt 0 ]; do
  case "$1" in
    --profile|--config)
      [ "$#" -ge 2 ] || { echo "Missing value for $1" >&2; exit 1; }
      if [ "$1" = --profile ]; then profile="$2"; else config_file="$2"; fi
      shift 2 ;;
    --prepare-only) prepare_only=true; shift ;;
    --skip-login) skip_login=true; shift ;;
    --device-code) device_code=true; shift ;;
    --help)
      echo "Usage: bash setup-aws.sh [--profile hackweek-a|hackweek-b] [--config /absolute/path] [--device-code] [--skip-login] [--prepare-only]"
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done
case "$profile" in
  hackweek-a) account=632472162753 ;;
  hackweek-b) account=552808407519 ;;
  *) echo "Select hackweek-a or hackweek-b." >&2; exit 1 ;;
esac
case "$config_file" in /*) ;; *) echo "--config/AWS_CONFIG_FILE must be an absolute path." >&2; exit 1 ;; esac
if [ -n "${OIC_MANIFEST_PATH:-}" ]; then
  echo "This script is for local SSO. Managed cloud authentication/setup is separate; preserve its injected config." >&2
  exit 1
fi

command -v brew >/dev/null 2>&1 || { echo "Homebrew is required. Install it from https://brew.sh, then rerun." >&2; exit 1; }
export PATH="$(brew --prefix)/bin:$PATH"
cli_v2() { command -v aws >/dev/null 2>&1 && [[ "$(aws --version 2>&1)" == aws-cli/2.* ]]; }
if ! cli_v2; then brew install awscli; fi
cli_v2 || { echo "Homebrew's AWS CLI v2 must be first on PATH." >&2; exit 1; }

export AWS_CONFIG_FILE="$config_file" AWS_PROFILE="$profile"
export AWS_REGION=us-west-2 AWS_DEFAULT_REGION=us-west-2 AWS_STS_REGIONAL_ENDPOINTS=regional AWS_PAGER=""
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
unset AWS_ROLE_ARN AWS_WEB_IDENTITY_TOKEN_FILE AWS_ROLE_SESSION_NAME
unset AWS_ENDPOINT_URL AWS_ENDPOINT_URL_STS
# Refuse invalid existing INI before replacing our three sections.
aws configure list-profiles >/dev/null
mkdir -p "$(dirname "$config_file")"
if [ -L "$config_file" ]; then
  echo "Config is a symlink. Pass --config with its resolved target to preserve the link." >&2
  exit 1
fi
temporary="$(mktemp "$(dirname "$config_file")/.hackweek-config.XXXXXX")"
trap 'rm -f "$temporary"' EXIT
if [ -f "$config_file" ]; then
  awk '
    /^[[:space:]]*\[/ { keep = ($0 !~ /^[[:space:]]*\[(sso-session kikoff-hackweek|profile hackweek-a|profile hackweek-b)\]/) }
    !/^[[:space:]]*\[/ && NR == 1 { keep = 1 }
    keep { print }
  ' "$config_file" > "$temporary"
fi
printf '\n' >> "$temporary"
cat >> "$temporary" <<'CONFIG'
[sso-session kikoff-hackweek]
sso_start_url = https://d-92670da180.awsapps.com/start
sso_region = us-west-2
sso_registration_scopes = sso:account:access

[profile hackweek-a]
sso_session = kikoff-hackweek
sso_account_id = 632472162753
sso_role_name = hackweek-administrator
region = us-west-2
output = json

[profile hackweek-b]
sso_session = kikoff-hackweek
sso_account_id = 552808407519
sso_role_name = hackweek-administrator
region = us-west-2
output = json
CONFIG
AWS_CONFIG_FILE="$temporary" aws configure list-profiles >/dev/null
chmod 600 "$temporary"
mv "$temporary" "$config_file"
trap - EXIT
echo "Profiles ready in $config_file. Selected $profile ($account)."

if ! $prepare_only; then
  if ! $skip_login && ! aws sts get-caller-identity --region us-west-2 >/dev/null 2>&1; then
    login_args=(sso login --profile "$profile")
    if $device_code; then login_args+=(--use-device-code --no-browser); fi
    aws "${login_args[@]}"
  fi
  identity="$(aws sts get-caller-identity --region us-west-2 --query '[Account,Arn]' --output text)" || {
    echo "STS failed: check SSO login/assignment and the connection error above. See CLAUDE.md." >&2
    exit 1
  }
  read -r actual_account arn <<< "$identity"
  if [ "$actual_account" != "$account" ] || [[ "$arn" != "arn:aws:sts::$account:assumed-role/AWSReservedSSO_hackweek-administrator_"*/* ]]; then
    echo "Wrong identity: require $account / hackweek-administrator. Never fall back to management/operator credentials." >&2
    exit 1
  fi
  echo "Verified participant: $arn"
fi
echo 'For subsequent commands:'
echo 'unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_ROLE_ARN AWS_WEB_IDENTITY_TOKEN_FILE AWS_ROLE_SESSION_NAME AWS_ENDPOINT_URL AWS_ENDPOINT_URL_STS'
printf 'export PATH=%q AWS_CONFIG_FILE=%q AWS_PROFILE=%q AWS_REGION=us-west-2 AWS_DEFAULT_REGION=us-west-2 AWS_STS_REGIONAL_ENDPOINTS=regional\n' "$PATH" "$config_file" "$profile"
echo "This setup has created no AWS resources."
