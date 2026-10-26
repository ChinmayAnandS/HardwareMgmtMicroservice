# Azure deployment runbook

Run from the repository root in Bash with Python 3.12, current Azure CLI, Docker, Atlas CLI and mongosh installed. Azure and MongoDB Atlas are separate control planes: **Azure CLI cannot create an Atlas cluster**. The included scripts use Azure CLI for Azure and Atlas CLI for Atlas. Provisioning requires Azure resource/role creation permission and Entra application/user-role assignment permission. A student subscription may restrict regions, NAT gateway or available quotas; check your actual subscription before starting billable deployment.

## 1. Select account and provision the foundation

```bash
az login
az account list --query '[].{name:name,id:id,state:state}' -o table
read -rp 'Azure subscription ID from the table: ' subscription
az account set --subscription "$subscription"
az account show --query '{name:name,id:id,tenant:tenantId}' -o table
bash infra/bootstrap.sh dev centralus
```

`bootstrap.sh` registers providers, creates the resource group, paid ACR Basic registry, user-assigned runtime identity, Key Vault, Log Analytics, Application Insights, VNet, NAT gateway, static public IP and a Container Apps workload-profiles environment. Apps themselves use its Consumption profile. It disables ACR admin credentials and grants the runtime only `AcrPull` and `Key Vault Secrets User`. The signed-in operator receives Key Vault Secrets Officer. Shell tracing must stay off. Resource names are derived from subscription and environment, without hard-coded account IDs.

Read the CLI output for the resource names and fixed egress IP. Reuse this subscription and environment in subsequent commands. Role assignments may require several minutes to propagate; retry the failed command after propagation, without creating duplicate Entra applications. The bootstrap script is a first-provisioning script; it does not promise an atomic rollback or universal rerun idempotence.

For production use `prod` instead of `dev`; this creates a separate resource group/network/registry/vault/identity. Use a distinct Atlas project and a separate Entra application as well. Do not point dev at production data.

## 2. Create Atlas and the restricted runtime identity

Choose an Atlas organization/project with billing configured. `US_CENTRAL` is Atlas's region label for Azure Central US; use the actual Atlas provider-region list if deploying elsewhere. The script below creates a **paid M10 cluster with backups** even for cloud dev; local Docker is cheaper for everyday development.

```bash
read -rp 'MongoDB Atlas project ID: ' atlas_project
bash infra/atlas.sh dev "$atlas_project" US_CENTRAL
```

The script authenticates interactively, creates a three-member M10 replica set, waits for readiness, allow-lists only the Azure NAT public IP `/32`, creates `hardwareApp`, and creates `hardware_app` with that role scoped to this cluster. When prompted, use a generated password from your password manager. Custom roles are created through Atlas's supported control plane, not `db.createRole()` in mongosh. The local Compose initialization uses native MongoDB roles because it is not Atlas.

The role grants:

| Collections | Allowed runtime actions |
|---|---|
| hardware, allocations, checkouts | FIND, INSERT, UPDATE |
| audit, requests | FIND, INSERT |
| sessions, limits | FIND, INSERT, UPDATE, REMOVE |

Do **not** add readWrite/database-owner roles to the runtime user: MongoDB permissions are additive and that would permit audit tampering.

For initial schema/index creation, use a temporary provisioning identity and a narrowly allow-listed trusted workstation or corporate egress IP. It needs readWrite and dbAdmin on database `hardware` only.

```bash
read -rp 'Trusted provisioning workstation public IPv4 address: ' operator_ip
atlas accessLists create "$operator_ip/32" --type cidrBlock --projectId "$atlas_project" --comment 'Temporary provisioning workstation'
atlas dbusers create --username hardware_provision --role readWrite@hardware,dbAdmin@hardware --projectId "$atlas_project" --scope hardware-dev
```

Use the Atlas cluster's **password-free SRV URI** from Connect, e.g. its `mongodb+srv` hostname plus `/hardware`, and let mongosh prompt for the provisioning password:

```bash
read -rp 'Atlas SRV URI WITHOUT username or password, with /hardware database: ' provisioning_uri
mongosh "$provisioning_uri" --username hardware_provision --authenticationDatabase admin --password --eval 'require("./scripts/schema.js")(db.getSiblingDB("hardware"))'
atlas dbusers delete hardware_provision --projectId "$atlas_project" --force
atlas accessLists delete "$operator_ip/32" --projectId "$atlas_project" --force
```

Run mongosh from the repository root so the schema module path resolves. Provisioning commands verify collection creation/validation/indexes; the API does not require schema-owner permissions. Verify the NAT IP remains allow-listed after removing the workstation IP.

Store the **application** SRV connection URI through a masked prompt:

```bash
python scripts/store-mongo-uri.py dev
```

Use `hardware_app` credentials and `authSource=admin`; URI-encode password characters using your password manager or connection builder. Atlas SRV connections use TLS and certificate verification. The helper refuses disabled TLS and writes the secret to Key Vault through an ephemeral private file; it never prints the URI. Neither the application nor deployment workflow needs the provisioning password.

Production M10 backups are enabled by the cluster creation script. Set backup retention in Atlas to your recovery requirements, configure Atlas storage/connection/replication alerts and complete an isolated restore drill. Hardware audit retention and idempotency receipts consume storage permanently in this implementation. For development-only Atlas Free/Flex comparisons see COSTS.md; feature/region availability must be checked before choosing them. They are not provisioned by this production runbook.

## 3. Configure Entra ID and administrative RBAC

```bash
python infra/identity.py dev
```

This creates a single-tenant web application with exact callback `https://hardware-ui.<environment-default-domain>/auth/callback`, an enterprise service principal, two application roles, and a one-year confidential client credential. The script assigns the invoking user `Hardware.Admin`, enables assignment-required, writes the credential directly to Key Vault and stores only client ID/issuer/origin in an ignored local setup file. If your tenant denies app-role assignment, a tenant administrator must run the assignment with the required Graph privileges. Do not replace it with an unverified group or email allow-list.

Assign further administrators or read-only administrative auditors under Entra Enterprise applications → hardware-dev → Users and groups → assign role. Require MFA through your tenant's supported security defaults or Conditional Access licensing. App roles are checked on the server; hiding UI buttons does not grant or enforce permission. Role changes require a new session or the fixed one-hour session expiry. For immediate revocation remove affected session documents with a controlled operations identity and revoke Entra sign-in as appropriate.

The login flow validates OIDC signature, issuer, audience, nonce and state using Authlib, and uses PKCE S256. OAuth tokens are consumed on the backend and discarded. The server session contains only identity, roles, CSRF nonce and temporary login state. Rotate the client credential before expiration. Save the new value under the same Key Vault secret name; restart a revision after secret refresh if necessary.

## 4. Deploy the API and UI

First run the full local tests and scans described in README.md on a machine with Docker and internet access. Then deploy:

```bash
python infra/deploy.py dev
```

The script builds separate API/UI containers, pushes them to ACR using your Azure login, resolves their content digests and deploys each by digest. It configures:

- User-assigned ACR pull identity, with no registry password.
- API ingress internal to the Container Apps environment; HTTPS, 8000 target port.
- UI external HTTPS ingress; 8080 target port; same-origin proxy to internal HTTPS API with upstream certificate verification.
- Key Vault references for Mongo URI and OIDC secret, resolved using runtime managed identity.
- Non-secret configuration for the correct Entra issuer, client ID and public UI origin.
- Fixed `minReplicas=0`, `maxReplicas=3`, 0.25 vCPU / 0.5 GiB per app, and HTTP concurrency target 20.
- Startup/liveness/readiness probes; Application Insights sampled at 10%; Log Analytics retained 30 days with a 1 GB/day ingestion cap.

Two apps can have separate cold starts. Their ingress routes wake them on demand. A maximum of three replicas limits compute exposure but is not a total spending cap. MongoDB pools are bounded at 20 connections per API replica. Key Vault secret references, log collection and network access still require correct subscription/provider permissions.

Obtain the actual URL and verify both unauthenticated boundaries and authenticated behavior:

```bash
ui_host=$(az containerapp show -g hardware-dev -n hardware-ui --query properties.configuration.ingress.fqdn -o tsv)
curl --fail --retry 12 --retry-delay 5 --retry-all-errors "https://$ui_host/health"
curl --fail --retry 12 --retry-delay 5 --retry-all-errors "https://$ui_host/ready"
curl -s -o /dev/null -w '%{http_code}\n' "https://$ui_host/api/hardware"
```

Expect health/readiness 200 and unauthenticated inventory 401. Open the UI, use Entra sign-in, create a small test pool, reserve it to a test project ID, process a return using an administrative integration and inspect the audit. Confirm browser cookies show `Secure`, `HttpOnly`, `SameSite=Lax`, with the `__Host-hardware` name. Confirm local login returns 404 in production and the API has no external ingress. Do not label a deployment successful solely because resource creation finished.

## 5. Monitoring, budgets and logs

```bash
read -rp 'Operations alert email: ' operations_email
bash infra/monitoring.sh dev "$operations_email"
az containerapp logs show -g hardware-dev -n hardware-api --type console --tail 50
az monitor log-analytics workspace show -g hardware-dev -n hardware-dev --query '{retention:retentionInDays,cap:workspaceCapping.dailyQuotaGb}' -o json
```

Metrics alerts notify the action group if either app exceeds five HTTP 5xx responses in five minutes. Application Insights records sampled application requests, dependencies and exceptions. No request bodies, cookies, authorization headers or OIDC callback URLs are written by access logs. Application audit is committed in MongoDB, independent of telemetry sampling. Azure logging ingestion caps can remove troubleshooting evidence once reached; they do not stop resource spending. Review Entra and Atlas control-plane audit events separately.

Use Azure Cost Management → Budgets to add an environment resource-group monthly budget and alerts at 50%, 80% and 100% of the approved amount. A budget is an alert, not an automatic kill switch. Track NAT, static IP, ACR, Atlas, backup, logging, request and compute charges together. Atlas billing is separate unless your organization has marketplace billing configured.

## 6. GitHub OIDC deployment

Create GitHub environments named exactly `dev` and `prod`. Protect prod with required reviewers and main-only deployment branches. Run for each environment:

```bash
read -rp 'GitHub repository as owner/repository: ' github_repository
bash infra/github-oidc.sh dev "$github_repository"
```

Copy the printed non-secret values into that GitHub environment's **Variables**: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `AZURE_ACR`. The issuer is GitHub Actions and the exact subject binds to `repo:owner/repository:environment:dev` (or prod). No client secret or database URI enters GitHub.

The deploy identity has Contributor scoped to its environment resource group and AcrPush on its registry. It does not have Owner, User Access Administrator or Key Vault secret data access. It updates existing app images and cannot grant itself new data-plane access. Treat an environment-approved deployment identity as privileged because it can change runtime code. Restrict who can approve prod deployments.

Pull requests run lint, dependency audit, unit/integration/browser tests, coverage and image scans; they cannot deploy. Pushes to develop target dev; pushes to main target prod after environment rules. The workflow transfers the verified image archive to the deployment job, pushes versioned SHA tags and deploys immutable digests. It does not rebuild after scans. The initial infrastructure provision is deliberately separate from ordinary code deployment.

Protect workflows with review. Before adopting in a regulated environment, pin GitHub Actions and base images to organization-approved commit/digest values using Dependabot-reviewed updates. The supplied version tags are verified by vulnerability scanning but are not a complete software supply-chain attestation system.

## 7. Rollback and secret rotation

Identify a previous good revision and image; do not overwrite a mutable tag:

```bash
az containerapp revision list -g hardware-dev -n hardware-api -o table
read -rp 'Previously verified full API image reference with @sha256 digest: ' previous_api_image
az containerapp update -g hardware-dev -n hardware-api --image "$previous_api_image" -o none
```

Repeat for UI if necessary. In Single revision mode a new healthy revision replaces the prior one. Inspect readiness/real administrative flows after rollback. Persisted inventory is not rolled back with code. This version has no destructive migration; future schema changes must maintain compatibility across the rollback window.

Rotate Atlas passwords with overlapping users or a controlled maintenance procedure, update `mongo-uri` in Key Vault via the helper and deploy/restart API replicas. Revoke the old user after confirming the new connection. Update app credentials using `az ad app credential reset --append` with output captured directly into a private file or secret store, then remove the retired credential. Do not paste password-bearing commands into shell history.

## 8. Cleanup

Export any required records and verify retention obligations before deleting production resources. Removing the Azure resource group does **not** remove Atlas, its backups or the Entra app. Run only for the environment you intend to destroy:

```bash
az group delete --name hardware-dev --yes --no-wait
read -rp 'Atlas project ID for this dev environment: ' atlas_project
atlas clusters delete hardware-dev --projectId "$atlas_project" --force
read -rp 'Entra application client ID for this dev environment: ' client_id
az ad app delete --id "$client_id"
```

Inspect Atlas backups/billing, network access lists, users and project cleanup after cluster deletion. Remove GitHub environment variables/federated deployment access when decommissioning. Key Vault soft-delete retains vault metadata for seven days; purge protection, if enabled by organizational policy, prevents immediate purge. Retain backups according to policy; do not assume they stop billing automatically.
