# Deployment record: PR Token Ledger

| | |
|---|---|
| Sandbox | A (`hackweek-a`), account `632472162753`, `us-west-2` |
| Project slug | `pr-token-ledger` |
| Owner / deployer | yifan.zhang@kikoff.com |
| Deployed | 2026-10-08 |
| URL | https://yifan-zhang-pr-token-ledger.a.hackweek.kikoff.dev (Twingate required) |
| Expected response | `GET /healthz` returns `200` with body `ok pr-token-ledger` |
| Verified from | Yifan's Mac with the Kikoff Twingate client connected, `curl --noproxy '*' --fail https://.../healthz` |
| Cleanup deadline | **TBD: record the date agreed at Hackweek kickoff here** |

Architecture: one Python 3.13 Lambda (stdlib + boto3) behind an internal ALB with the
shared wildcard certificate. The database is a single SQLite file in a private S3
bucket; the function runs with reserved concurrency 1 so writes never race.
No VPC attachment, no public endpoints, no new security groups.

## Resources (all tagged project=pr-token-ledger, owner=yifan.zhang@kikoff.com, created-by=yifan.zhang@kikoff.com)

Exact IDs are in `deploy/state.env` (written by `deploy/deploy.sh`).

| Resource | Name / ID |
|---|---|
| S3 bucket (private, public access blocked) | `hackweek-yifan-zhang-pr-token-ledger-data` |
| SSM SecureString (admin token) | `/hackweek/yifan-zhang/pr-token-ledger/admin-token` |
| SSM SecureString (session cookie signing secret) | `/hackweek/yifan-zhang/pr-token-ledger/session-secret` |
| SSM SecureString (GitHub OAuth client id/secret; written by `deploy/set-github.sh`) | `/hackweek/yifan-zhang/pr-token-ledger/github-oauth` |
| IAM role (permissions boundary attached) | `hackweek-yifan-zhang-pr-token-ledger-lambda` |
| CloudWatch log group (7-day retention) | `/aws/lambda/hackweek-yifan-zhang-pr-token-ledger` |
| Lambda | `hackweek-yifan-zhang-pr-token-ledger` (arm64, 512 MB, 30 s, reserved concurrency 1) |
| Target group (lambda) | `hackweek-yifan-zhang-ledger-tg` |
| ALB (internal, shared workload SG, private app subnets) | `hackweek-yifan-zhang-ledger` |
| HTTPS listener 443 | shared cert `*.a.hackweek.kikoff.dev`, `ELBSecurityPolicy-TLS13-1-2-2021-06` |
| Route 53 alias (private zone `Z0108043AWPSA7N5M3A3`) | `yifan-zhang-pr-token-ledger.a.hackweek.kikoff.dev` |

No `.internal` HTTP record was created; HTTPS only. Nothing untaggable was created.

## Sign-in

GitHub OAuth (web: authorization code; CLI: device flow) once `deploy/set-github.sh` has
stored the OAuth App's client id and secret; until then the site falls back to token
sign-in. Lambda calls github.com directly (not VPC-attached), so no network changes.
Status: **waiting for the OAuth App to be created under Yifan's GitHub account.**

## Operate

```bash
bash deploy/deploy.sh update     # push new app code
bash deploy/deploy.sh            # re-run full deploy (idempotent)
aws logs tail /aws/lambda/hackweek-yifan-zhang-pr-token-ledger --profile hackweek-a --since 30m
```

Reset a teammate's token (admin only, needs AWS access):

```bash
export AWS_PROFILE=hackweek-a
ADMIN=$(aws ssm get-parameter --name /hackweek/yifan-zhang/pr-token-ledger/admin-token --with-decryption --query Parameter.Value --output text)
curl --noproxy '*' -X POST -H "Authorization: Bearer $ADMIN" -H 'content-type: application/json' \
  -d '{"email":"someone@kikoff.com"}' https://yifan-zhang-pr-token-ledger.a.hackweek.kikoff.dev/api/admin/reset-user
```

## Cleanup (by the agreed deadline)

```bash
bash deploy/cleanup.sh --yes
```

Deletes only the resources above, in dependency order. Never touches the shared
private zone, certificate, VPC, subnets or security group. Estimated running cost
while deployed: about $17/month for the ALB plus cents for Lambda, S3 and logs.
