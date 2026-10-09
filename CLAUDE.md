# Hackweek

For an Oasis/Kikoff frontend change, use a draft PR and the existing Vercel preview.
For an existing backend change, follow that app's Flightcontrol/Ravion preview guide.
Kikoff Flightcontrol currently needs a non-draft PR to master with deploy-preview;
its preview URL requires Twingate. Check the app guide before triggering deployment.

For standalone demos, use a shared AWS sandbox. Console, CLI, AI and optional
IaC are all supported; choose the smallest setup that works. Both sandboxes are
already deployed. Use the account recorded in the demo repository; if none is
recorded, use sandbox A and record the account, project slug and owner there.
Use sandbox B when the project owner assigns it. Do not ask for account numbers
or foundation values: they are below.

Use the `hackweek-deploy` skill at `.agents/skills/hackweek-deploy/SKILL.md` in the
infrastructure repository. For a separate demo repository, copy this guide and its
AGENTS.md symlink, plus `setup-aws.sh`; the skill is an optional discovery aid,
not a dependency for following this guide. Install/copy the skill into the demo's
`.agents/skills/` if desired, updating its guide link to the copied CLAUDE.md.
Keep demo code/deployment records separate from the foundation Pulumi program.

## Start from an empty local machine

Run this workflow in local Codex on the user's Mac/Linux machine, with a writable
home directory. Managed cloud configuration is separate. Missing CLI tooling or
AWS config is setup work; do not stop at a localhost preview.

Run the setup script using its absolute path (replace `/path/to/hackweek`):

```bash
bash /path/to/hackweek/setup-aws.sh --profile hackweek-a
```

It uses Homebrew to install AWS CLI v2 if needed, preserves unrelated profiles,
writes the two Hackweek profiles, reuses a valid session or signs in, and verifies
the participant account/role. If Homebrew is missing, help the human install it from [brew.sh](https://brew.sh)
first. No Node, Python, Pulumi, Mise or bundled binaries are needed for setup.
The script prints PATH and AWS exports for subsequent command sessions.

Let the human complete the browser login. Use `--device-code` if the browser cannot
open, `--skip-login` to check an existing session, or `--prepare-only` to write
profiles without authenticating. The script prints the exports for subsequent
commands; use them and clear inherited static/web-identity credentials as below.
If account/role assignment is missing after login, ask Infra; configuring a profile
cannot grant access. Do not use operator credentials to get past a denial.

The user also needs the normal Kikoff Twingate client installed and connected.
On a Mac where it is missing, run `brew install --cask twingate`, then
`open -a Twingate`. Use network name **kikoff**, and let the human complete company
login and any OS VPN/system-extension prompts. Reuse an existing Kikoff client
setup; do not create service-account keys or new Twingate grants.

AWS SSO authenticates deployment commands; Twingate makes the finished private
URL reachable. Verify both independently. Installing app-specific tools (for
example Docker for an ECS container build) is agent work once AWS access works.

## AWS profile setup

The setup script writes this config. For manual setup, use AWS CLI v2 and merge
these sections into `~/.aws/config` (or the file selected by
`AWS_CONFIG_FILE` if set). Preserve unrelated profiles; update matching sections
instead of adding duplicate section names. These settings contain no credentials.

```ini
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
```

For local SSO, select the assigned profile, sign in, and verify the identity before
provisioning or deleting anything:

```bash
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
unset AWS_ROLE_ARN AWS_WEB_IDENTITY_TOKEN_FILE AWS_ROLE_SESSION_NAME
unset AWS_ENDPOINT_URL AWS_ENDPOINT_URL_STS
export AWS_PROFILE=hackweek-a # Change to hackweek-b for sandbox B.
export AWS_REGION=us-west-2
export AWS_DEFAULT_REGION=us-west-2
export AWS_STS_REGIONAL_ENDPOINTS=regional
case "$AWS_PROFILE" in
  hackweek-a) export HACKWEEK_ACCOUNT_ID=632472162753 ;;
  hackweek-b) export HACKWEEK_ACCOUNT_ID=552808407519 ;;
esac
export HACKWEEK_BOUNDARY_ARN="arn:aws:iam::${HACKWEEK_ACCOUNT_ID}:policy/hackweek-permissions-boundary"
aws sso login --profile "$AWS_PROFILE"
aws sts get-caller-identity --profile "$AWS_PROFILE"
```

The returned `Account` must equal `HACKWEEK_ACCOUNT_ID`, and `Arn` must contain
`assumed-role/AWSReservedSSO_hackweek-administrator_`. Stop on an identity mismatch.
AWS CLI/SDK SSO authentication automatically assumes that role; no separate
`aws sts assume-role`, static keys or `source_profile` are needed. Never use
`OrganizationAccountAccessRole`, management account `366185084586` or another
account's credentials to deploy a demo. Reuse this profile and explicit region for
every CLI, SDK and IaC operation; check any tool-specific provider configuration.

If SSO expires, rerun the login command. If the browser cannot open, use
`aws sso login --profile "$AWS_PROFILE" --use-device-code --no-browser` and let the
human complete the displayed login. Access is initially Foundations/Security
only. If the account/role is not assigned, ask Infra for access; changing the
profile cannot grant it. SCP/boundary denials require a reviewed exception, not
operator credentials. See [AWS CLI SSO setup](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html).

## Deployed foundation values

Verified against AWS on October 1, 2026. Use these existing resources instead of
creating VPCs, subnets or security groups. If a resource no longer exists, stop
and ask Infra to refresh its published value; do not substitute another account
or network. Do not read the management account's Pulumi backend.

| Value | Sandbox A (`hackweek-a`) | Sandbox B (`hackweek-b`) |
| --- | --- | --- |
| `accountId` | `632472162753` | `552808407519` |
| `vpcId` | `vpc-09f7e657a49f12dbc` | `vpc-0f0741a6df05b3898` |
| `privateSubnetIds` | `subnet-0f12bcf8bb29245a6`, `subnet-096bef3bec563ef8d` | `subnet-0e1f2acb4027d429b`, `subnet-0cbe55e77aa88795c` |
| `databaseSubnetIds` | `subnet-017f85f9cbb31b617`, `subnet-0eca34723823afb43` | `subnet-05898c15a4f1787f6`, `subnet-06a42e85dd77d8215` |
| `workloadSecurityGroupId` | `sg-0be56bdc1d1df9d0e` | `sg-037cc04918927d44d` |
| `privateDnsZoneId` | `Z104282810RBX2RG5FTYW` | `Z09063922RYKWCT7OT7HJ` |
| `privateDnsDomain` | `hackweek-sandbox-a.internal` | `hackweek-sandbox-b.internal` |
| `permissionsBoundaryArn` | `arn:aws:iam::632472162753:policy/hackweek-permissions-boundary` | `arn:aws:iam::552808407519:policy/hackweek-permissions-boundary` |

## HTTPS and existing HTTP names

HTTP apps use HTTPS by default. Infra provides a private DNS zone and an ACM
wildcard certificate in each sandbox; each app owns its ALB listener and DNS
record. No app manifest or HTTPS flag is needed.

| Value | Sandbox A (`hackweek-a`) | Sandbox B (`hackweek-b`) |
| --- | --- | --- |
| `httpsDnsDomain` | `a.hackweek.kikoff.dev` | `b.hackweek.kikoff.dev` |
| Certificate domain | `*.a.hackweek.kikoff.dev` | `*.b.hackweek.kikoff.dev` |

Discover the existing HTTPS resources with the participant profile after the
foundation has been deployed:

```bash
case "$AWS_PROFILE" in
  hackweek-a) export HACKWEEK_HTTPS_DOMAIN=a.hackweek.kikoff.dev ;;
  hackweek-b) export HACKWEEK_HTTPS_DOMAIN=b.hackweek.kikoff.dev ;;
  *) echo 'Select hackweek-a or hackweek-b first' >&2; exit 1 ;;
esac
export HACKWEEK_HTTPS_ZONE_ID="$(aws route53 list-hosted-zones-by-name \
  --profile "$AWS_PROFILE" --dns-name "$HACKWEEK_HTTPS_DOMAIN" \
  --query "HostedZones[?Name=='${HACKWEEK_HTTPS_DOMAIN}.' && Config.PrivateZone].Id | [0]" \
  --output text)"
export HACKWEEK_HTTPS_CERTIFICATE_ARN="$(aws acm list-certificates \
  --profile "$AWS_PROFILE" --region us-west-2 \
  --query "CertificateSummaryList[?DomainName=='*.${HACKWEEK_HTTPS_DOMAIN}' && Status=='ISSUED'].CertificateArn | [0]" \
  --output text)"
```

Both values must identify existing resources, not `None` or an empty result.
If they are missing, ask Infra to finish the HTTPS foundation; do not create a
replacement certificate or private zone. Verify the zone belongs to the published
VPC and the certificate is in the selected account and `us-west-2`.

For each HTTP app:

1. Add an HTTPS listener on port 443 to the app's **internal** ALB, using the
   shared certificate and `ELBSecurityPolicy-TLS13-1-2-2021-06`. Forward to the app's
   existing target group. The app can continue serving HTTP behind the ALB.
2. Add a private Route 53 alias in `HACKWEEK_HTTPS_ZONE_ID` for
   `<person>-<project>.<httpsDnsDomain>`, pointing to that same ALB. Keep the name
   to one label below the domain so the wildcard certificate covers it.
3. Preserve existing `.internal` DNS records and HTTP listeners. Apps may keep
   or add `http://<person>-<project>.<privateDnsDomain>` alongside the HTTPS URL.
   Do not redirect all port-80 traffic or try to serve trusted HTTPS on `.internal`;
   the public certificate covers only the new `kikoff.dev` names.
4. Verify HTTPS through Twingate without `curl -k`, and verify any retained HTTP
   URL still works. Update app origins, cookies or callback URLs if its own settings
   require the new hostname; keep old clients working.

Reuse the shared workload SG; it already permits port 443 from Twingate. Both DNS
wildcards are provisioned by Infra. Do not change Twingate grants, public DNS, or
shared certificates. Public DNS contains certificate-validation/CAA records and
namespace TXT markers that block the existing ngrok wildcard; app records stay
private. Record both URLs and the app-owned listener/record IDs
and cleanup steps in the demo repository. Never delete the shared HTTPS zone or
certificate during app cleanup.

## Sandbox rules

Use `us-west-2` regional S3 endpoints for bucket/object operations (for example,
`https://s3.us-west-2.amazonaws.com`). Global-endpoint object requests are
intentionally blocked by the region SCP.

Use synthetic data only; never import production credentials, secrets or presigned
URLs. These accounts are intentionally multi-tenant: other developers and agents
share them, and projects are not isolated from each other. Behave accordingly.
Namespace every project-owned resource as `hackweek-<person>-<project>-...`.
Use the deploying person's email local part, normalized to lowercase letters,
digits and hyphens, for `<person>`; for example `ilan.ponimansky@kikoff.com`
becomes `ilan-ponimansky`, yielding `hackweek-ilan-ponimansky-my-demo-...`.
Use the verified SSO session identity to identify the deployer when its session
name is a company email. Otherwise confirm the human's identity; do not invent
an email from an arbitrary STS session name.
Respect service
name limits; shorten slugs/suffixes as needed while keeping the person identifiable.
Do not use generic names such as `demo`, `test` or `app` by themselves.
Tag resources as you create them with `project=<project-slug>`,
`owner=<responsible-person-email>` and `created-by=<deploying-person-email>`.
Use real people, not `platform`, `shared` or the agent's
name. Propagate these tags to tasks, volumes and other child resources where the
service supports it, so compute and spend can be traced to the person who deployed it.
If a service cannot tag at creation, tag immediately afterward; record ownership
and cleanup steps for untaggable resources in the demo repository.

Start with the smallest resources that can run the demo. Do not create large or
expensive resources unless they are necessary for the project's actual workload.
Avoid giant EC2/database instance classes, GPUs, large storage allocations, high
replica counts and high-throughput Bedrock models by default. Keep autoscaling
maximums small. Scale up only when a measured requirement justifies it; explain
the size, expected cost and cleanup plan to the project owner before provisioning
expensive capacity. A reference architecture or an agent's guess is not enough.
The $500 monthly budget is an alert, not a spending limit.

Change or delete only resources known to belong to this project. Check ownership
tags and the project's deployment records before a destructive action. Missing
tags, an old creation date, an idle resource or admin access do not imply permission
to delete it. If ownership or deletion intent is unclear, ask the owner or Infra.
Never run account-wide cleanup or delete another project's resources. Preserve
shared foundations.

Hackweek access and workloads are limited to one week, at most seven days. Record
the shared cleanup deadline agreed at Hackweek kickoff and remove only this
project's resources by then. Starting a new demo does not extend the week. The
project owner is responsible for workload cleanup; Infra revokes access and owns
foundation teardown. The AWS accounts remain company-owned. The deadline does not
authorize an agent to close accounts; account closure requires a separate review.
If kickoff has not published a deadline, ask for it while continuing setup and the
demo. Do not invent a rolling seven-day deadline or require an expiry environment
variable on every command. An authorized disposable test can be removed immediately
after verification while the shared date is being confirmed. Do not leave an
unattended workload running without an agreed cleanup plan.

Use the published VPC: private subnets for apps/internal load balancers and isolated
subnets for databases. The egress subnets are for NAT only. Attach the published
workloadSecurityGroupId to apps, internal load balancers, targets and databases.
It admits the connector and other workloads in that SG; projects share trust.
Do not create or edit VPCs, subnets, routes, gateways, endpoints, ACLs or SGs.
Normal workload network-interface creation/attachment/deletion is allowed.
Use a record in the published private DNS zone and browse with Twingate connected.
Serve static files on EC2/ECS; HTTP Lambda apps use an internal ALB Lambda target
(no VPC attachment needed just for ingress). Only ALB Lambda permission grants are
allowed initially; other push triggers need an exception. Public managed hosting,
Lambda URLs and public DNS are blocked. Use the shared HTTPS path above for HTTP
apps; existing `.internal` HTTP endpoints remain supported. Keep S3 private. Store secrets
in Secrets Manager or SSM SecureString and keep credentials out of files/logs.

## Deployment recipes

Use the existing network and workload SG explicitly. Many IaC examples create
VPCs or SGs automatically; disable that behavior. The SCPs prohibit those writes.
Keep deployment files and cleanup steps in the demo's repository so the next
agent can continue. Console, AWS CLI and optional IaC are all acceptable.
For a basic Hello World, use the CLI directly; a custom deployment framework,
CloudFormation change-set runner or a new Pulumi backend is unnecessary. Create
the bounded role, function, target group/ALB, invocation permission, listener and
private DNS record. Record each returned ID as it is created so a partial failure
can be resumed or cleaned up. An authorized project owner can hand off to another
deployer; preserve ownership and record the new deployer without requiring the
original creator's session for every update. Install only the tools this app needs
(for example Docker for container builds); complete CLI/config setup before
spending time building deployment automation.

Every execution/application/deployment role must use `permissionsBoundaryArn`
at creation: select it under Permissions boundary in the console, pass
`--permissions-boundary` to `aws iam create-role`, set `permissionsBoundary` on
Pulumi `aws.iam.Role`, or `PermissionsBoundary` on CloudFormation `AWS::IAM::Role`.
The boundary caps permissions; attach a separate least-privilege role policy to
grant what the app needs. Do not remove/replace the boundary or edit its policy.
AdministratorAccess remains capped by both this boundary and the SCPs. AWS-created
service-linked roles follow their service's separate lifecycle.
If a service's console wizard cannot set a boundary, create the bounded role in
IAM first and select the existing role in the wizard.

### ECS / static sites

1. Create a project-owned ECR repository in the assigned account and push a small
   container image. Match its architecture to the task definition's runtime platform.
2. Create a project-owned ECS cluster, CloudWatch log group and separate task
   execution/application roles. The execution role needs ECR pull and log-write
   permissions; the application role gets only its own required AWS permissions.
3. Register a Fargate task definition with `awsvpc` networking, explicit container
   ports, logs and a small valid CPU/memory pair (for example 256 CPU / 512 MiB).
4. Create the service in the published private app subnets with the fixed workload
   SG and `assignPublicIp: DISABLED`. For HTTP, create an **internal** ALB in those
   subnets using the same SG, an `ip` target group and an HTTPS listener using the
   shared certificate as described above. Attach the target
   group to the ECS service; set a health-check path the container actually serves.
5. Add a private Route 53 alias to the ALB under
   `<person>-<project>.<httpsDnsDomain>`. Preserve any existing `.internal` alias
   and HTTP listener. No new Twingate Resource or public DNS is needed. Check service events, healthy
   targets and logs, then browse with the Twingate client connected.

Serve static files from the container using nginx or another small HTTP server.
Keep any S3 asset bucket private; S3 website hosting and presigned downloads are
not the publishing path. Avoid NAT/connector/foundation changes when debugging.
See [Fargate networking](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-task-networking.html).

### Lambda

1. Create a project-owned execution role trusted by `lambda.amazonaws.com`, a
   log group and a small ZIP or same-account ECR image function. Give the role
   log-write access and only the application's required AWS permissions.
2. For background work, invoke with same-account IAM using the console/CLI/SDK;
   no URL or load balancer is required. For HTTP, create an **internal** ALB in the
   private app subnets with the fixed workload SG and a `lambda` target group.
3. Before registering the function, add its invocation permission for
   `elasticloadbalancing.amazonaws.com`, scoped by `SourceArn` to that target
   group's ARN and `SourceAccount` to the assigned account. Register the function
   target, create an HTTPS listener with the shared certificate forwarding to the
   target group, and add the private HTTPS DNS alias as described above. Preserve
   existing `.internal` records and HTTP listeners. Do not create a Lambda function URL or API Gateway.
4. Use the ALB event/response contract, rather than assuming an API Gateway event.
   A minimal text response is:

   ```json
   {"statusCode":200,"isBase64Encoded":false,"headers":{"content-type":"text/plain"},"body":"hello"}
   ```

5. VPC attachment is needed only for private dependencies, such as a database.
   If needed, use the private app subnets and fixed workload SG, plus the required
   Lambda ENI permissions on the execution role. ALB invocation itself does not
   require VPC attachment. Check invocation errors/logs and test through Twingate.

Other service-principal push triggers need an SCP exception; do not work around
that with external credentials or public endpoints. See
[ALB Lambda targets](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/lambda-functions.html).

## Verify the delivered endpoint

Finish deployment, not just app/template generation. Wait for Lambda's active
state, ALB availability, and DNS `INSYNC`. For ECS, wait for service stability and
healthy targets. Check that the ALB scheme is `internal` and its subnets/SG match
the published values. Test Lambda invocation directly for errors; ALB Lambda
targets may report health checks disabled, so that status alone is not failure.

Use the exact private hostname created for this demo, not the ALB hostname or a
localhost preview. From a machine with the normal Kikoff Twingate client connected:

```bash
export HACKWEEK_URL='https://<person>-<project>.a.hackweek.kikoff.dev'
curl --noproxy '*' --fail --show-error --max-time 20 "$HACKWEEK_URL/"
```

Substitute the recorded URL. Sandbox B HTTPS names end in `b.hackweek.kikoff.dev`.
Do not disable certificate validation. Check every retained `.internal` HTTP URL
as well, using the existing `http://` URL.

Require the expected status and content/marker, not merely any HTTP 200. The existing Twingate wildcard resolves the private name
through its connector. Desktop DNS can use Twingate synthetic IPs; they need not
equal the ALB's VPC addresses. Do not add public DNS or modify SGs for diagnosis.

| Symptom | Check next |
| --- | --- |
| CLI missing or SSO command unavailable | Run setup; verify CLI v2 and PATH, not just a pip package. |
| Login/token error | Complete or repeat SSO login on the executor that runs the commands. |
| Account/role not offered after login | Ask Infra for the participant assignment; a profile cannot grant it. |
| SCP/permissions-boundary denial | Inspect the denied action/resource and recipe; do not create a VPC/SG or remove the boundary. |
| Private name fails on the laptop | Check Twingate login/connection and DNS record propagation. |
| Name resolves but HTTP times out | Check listener, internal ALB/subnets/SG, then ask Infra to check the existing connector/grant. |
| HTTPS certificate error | Check the hostname, issued shared certificate and port-443 listener; `.internal` is not covered. |
| ALB returns 502/503 | Check function response contract/permission/registration or ECS healthy targets and app logs. |

Record the URL, expected response, verification machine/path, account/resource
IDs and project-owned cleanup steps. If private client access is unavailable,
report **deployed; Twingate verification pending** and supply the exact browser/
curl test for the human. If AWS access is unavailable, report **prepared; not
deployed**. Do not claim an endpoint works from localhost, unit tests, a template
check, environment readiness or an ALB status alone. Where an authorized outside
client is available, confirm it cannot reach the private origin.

## Guardrails and cleanup

Preserve hackweek:foundation=true resources, organization logging, SSO roles and
OrganizationAccountAccessRole. Do not connect to production data or private networks. Never expose services through
reverse tunnels, outside-account hosting or credentials. Outbound internet for
packages/APIs is allowed. AWS authenticated APIs/presigned URLs are outside the
private web-ingress controls (sandbox-signed S3 downloads are blocked); do not use them to publish demo data.
Use on-demand resources/inference; reservations, reserved capacity, general Marketplace
subscriptions and provisioned Bedrock throughput are blocked. Ask Infra for a
narrow SCP exception if a needed product is blocked. Bedrock on-demand inference
and subscriptions initiated through Bedrock are allowed; use US geographic
inference profiles. Start small, explain recurring costs and leave
cleanup commands or console steps with the project owner. Follow the agreed
shutdown deadline.
