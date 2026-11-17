# Production: releases, rollback and monitoring

Production is the existing Container Apps environment `managed-westus-1uv3hoo` in the resource
group `hardware-grpc`, with the apps `hardware-grpc-app` (the gRPC API other teams call) and
`hardware-grpc-webapp` (the website). Their URLs are shared with other teams, so nothing here
recreates them: Terraform **imports** them.

## What happens on a merge into `main`

`deploy-prod.yml` (when the repository variable `AUTO_DEPLOY_PROD` is `true`, or when you run it by hand):

1. **Build** both images from that commit and pin them by digest.
2. **Terraform** (`infra/terraform/stacks/prod`) applies the app shape, log storage and alerts.
3. **API rollout** (`infra/scripts/rollout.sh`), the careful part:
   1. All traffic is pinned to the revision serving today (the *old* one).
   2. A *new* revision is created from the new image. It receives **no** traffic.
   3. A smoke test runs against the new revision's private URL
      (`https://hardware-grpc-app---canary.<env domain>`): TLS, the gRPC health check, and `Ready` (which checks MongoDB).
   4. Traffic moves **10% > 50% > 100%**. After each step the pipeline waits (180 s by default) and checks:
      the revision is `Healthy`, it served at most 3 server errors (5xx), it restarted at most 2 times,
      and the public URL still passes the smoke test.
   5. **Any failed check sends 100% of traffic back to the old revision**, removes the canary label, deactivates the new revision, and fails the run.
   6. On success the old revision is kept (for an instant rollback) and anything older is deactivated.
4. **Website rollout**: the same, in one step (100%) after its own smoke test.

## Rolling back by hand

Actions > **rollback-prod** > Run workflow. Choose `api`, `web` or `both`. It sends all traffic to
the previous revision in seconds, with no build. To go to a specific revision, choose one app and
paste the revision name (shown in the *Revisions after the rollout* step of the deploy run).
From a terminal: `bash infra/scripts/rollback.sh hardware-grpc hardware-grpc-app`.

## Alerts and logs (all in Terraform)

Email alerts go to the `OPS_EMAIL` address for:

| Alert | Fires when |
|---|---|
| `hardware-api-5xx`, `hardware-web-5xx` | more than 5 server errors in 5 minutes |
| `hardware-api-request-spike` | more than 3000 requests in 5 minutes (tune `spike_requests_per_5m`) |
| `hardware-api-traffic-anomaly` | request volume far above the API's learned normal (needs a few days of history) |
| `hardware-api-inbound-bandwidth-spike` | more than 50 MB received in 5 minutes |
| `hardware-api-restarts` | the container restarted more than 3 times in 15 minutes |

Logs (console and system, including the `rpc=` and `[access]` lines) are now stored in the Log
Analytics workspace `hardware-prod-logs` for 30 days. Before this, the environment had no
diagnostic setting, so nothing was kept. In the portal: the workspace > Logs, for example:
```
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == "hardware-grpc-app" and Log_s has "status=INTERNAL"
| order by TimeGenerated desc
```

## What this does and does not protect against

- **Does:** bad images that crash or never become healthy, broken starts, a failed database
  connection (`Ready`), unreachable pods, restarts, and large traffic or bandwidth spikes (by alert).
- **Does not:** a bug that returns wrong answers or gRPC application errors. gRPC reports those as
  HTTP 200, so the 5xx gate cannot see them. Catching them needs application-level metrics
  (OpenTelemetry is already wired in `server.py`: set `APPLICATIONINSIGHTS_CONNECTION_STRING`).
- **Does not roll back the database.** Moving traffic back does not undo data changes. Keep schema
  changes backward-compatible (add first, remove in a later release).
- **Not a rate limiter or DDoS defence.** Alerts tell you about a spike; they do not stop it.
- **Alert-triggered rollback is not built.** The pipeline rolls back by itself during a release;
  after a release a person (or an alert email) starts `rollback-prod`.

## One-time setup (you run these; they need your Azure login)

1. `bash infra/terraform/bootstrap-prod.sh ChinmayAnandS/HardwareMgmtMicroservice`.
   It creates the `github-deploy-prod` identity (Contributor on `hardware-grpc` only) and the
   `tfstate-prod` state container, then prints the variables for step 3.
2. Tighten the dev identity, which was created before state was split. Remove its account-wide state
   access (the container-scoped one is added by re-running `bootstrap.sh`):
   ```
   az role assignment delete --assignee <dev identity principal id> --role "Storage Blob Data Contributor" --scope <state account id>
   ```
3. In GitHub, create the Environment `prod`, restrict *Deployment branches* to `main`, and add the
   variables printed by the script: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`,
   `TF_STATE_RESOURCE_GROUP`, `TF_STATE_STORAGE_ACCOUNT`, `TF_STATE_CONTAINER`, and `OPS_EMAIL`.
   No secrets are needed: the prod stack does not copy any credential, and prod keeps its
   environment variables where they are today (inside the apps).
4. **First apply, by hand.** It changes live apps (single > multiple revision mode) and imports them,
   so look at the plan first:
   ```powershell
   cd infra\terraform\stacks\prod
   terraform init "-backend-config=resource_group_name=hardware-tfstate" "-backend-config=storage_account_name=<TF_STATE_STORAGE_ACCOUNT>" "-backend-config=container_name=tfstate-prod" "-backend-config=key=prod.tfstate" "-backend-config=use_azuread_auth=true"
   terraform plan "-var=subscription_id=<subscription id>" "-var=ops_email=<alert email>"
   ```
   Expected: 2 to import, 9 to add, 2 to change (only `revision_mode`), 0 to destroy. Then `terraform apply` with the same variables.
5. Create the repository variable `AUTO_DEPLOY_PROD=true` when you want pushes to `main` to deploy.
   Until then, deploy by hand with Actions > deploy-prod > Run workflow.

## Known limits

- The canary logic has been linted and its metric parsing checked, but it has not been exercised against a real
  revision. Do a first release when a failed rollback would not matter, and watch it.
- Whether traffic is split evenly across long-lived gRPC connections depends on how the ingress balances them.
  Other teams' clients that keep one connection open may stay on one revision longer than the weights suggest.
- `min_replicas` is 0 (as it is today): the first call after an idle period waits for a cold start. Set the Terraform
  variable `min_replicas = 1` to keep the API warm (this uses more of your credit).
- Prod's environment variables (Mongo URI, team codes) are plain values inside the apps. Moving them to secrets is
  a good next step; Terraform deliberately ignores them for now.
