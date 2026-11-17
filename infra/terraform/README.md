# Terraform: dev previews

```
modules/hardware_apps/   the two Container Apps (gRPC API + website), reused by every stack
stacks/dev-preview/      one throwaway API + website pair per pipeline run
stacks/prod/             the live apps (imported), log storage and alerts
bootstrap.sh             one-time: state storage, dev resource group, GitHub OIDC identity and roles
```

`stacks/prod` adopts the existing production apps with `terraform import` (the environment's default
domain is part of the URL other teams use) and adds logging and alerts. See
[docs/PRODUCTION.md](../../docs/PRODUCTION.md) for how releases, canary rollout and rollback work.
`bootstrap-prod.sh` is its one-time setup.

## Why previews share the prod environment

gRPC needs HTTP/2 ingress, and that only works in a standard (workload-profiles) Container Apps
environment, not an "express" one. This subscription allows **one** standard environment in total
(`MaxNumberOfGlobalEnvironmentsInSubExceeded`) and only a few regions, and prod already uses it
(`managed-westus-1uv3hoo` in `hardware-grpc`). So previews are separate apps, in the
`hardware-dev-rg` resource group, attached to that environment. They get their own hostnames
(`hardware-api-<run id>.<env domain>`); prod's apps and URLs are not touched. The GitHub identity
holds a custom role that can only read and join that environment, and Contributor on
`hardware-dev-rg` only, so it cannot change or delete the environment or any prod app.

## One-time setup (you run these; they need your Azure login)

1. `az login`, then
   `bash infra/terraform/bootstrap.sh westus <github-owner>/<repo>`
   (optionally followed by the shared environment's name and resource group; the defaults are
   `managed-westus-1uv3hoo` and `hardware-grpc`). It is safe to re-run. At the end it prints the
   values for step 3.
2. If you created the old `hardware-dev-env` (an express environment) and its log workspace, delete
   them; nothing uses them any more:
   ```
   az containerapp env delete -g hardware-dev-rg -n hardware-dev-env --yes
   az monitor log-analytics workspace delete -g hardware-dev-rg -n hardware-dev-logs --yes --force
   az storage blob delete --auth-mode login --account-name <TF_STATE_STORAGE_ACCOUNT> -c tfstate -n dev-base.tfstate
   ```
3. In GitHub, create the Environment `dev` and add:
   - variables: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`,
     `TF_STATE_RESOURCE_GROUP`, `TF_STATE_STORAGE_ACCOUNT`, `TF_STATE_CONTAINER`
   - secret `ATLAS_DEV_MONGO_URI`: an SRV URI **without a database name** for a dev-only Atlas
     user that has `readWriteAnyDatabase` and `dbAdminAnyDatabase` (each preview gets its own
     database, `hw_<run id>`). Never reuse the production user or cluster.
   - secret `DEV_PREVIEW_ADMIN_CODE`: the `hardware-admin` team code for previews (16+ characters).
   - secret `DEV_PREVIEW_LOGIN_CODE`: the website dev sign-in code for previews (16+ characters).
   Fixed codes (instead of random ones) let you sign in during the manual-testing window without
   printing credentials into the logs, which anyone can read because the repository is public.
   Also set the Environment's *Deployment branches* to `dev` only: the Azure identity trusts
   anything that runs in this Environment.
4. Atlas dev project: allow `0.0.0.0/0` in the IP access list. GitHub runners and Container
   Apps have no fixed addresses. This is acceptable for a throwaway dev cluster only.
5. Make the two GHCR packages public (`<repo>` and `<repo>-web`), or the apps cannot pull them.

## What a push to `dev` does

Pushes deploy only while the repository variable `AUTO_DEPLOY_DEV` is `true` (a *repository*
variable under Settings > Secrets and variables > Actions > Variables, not an Environment one).
Documentation-only pushes never deploy. Running the workflow by hand always deploys.

`deploy-dev.yml` builds both images and pins them by digest, creates the database, applies
`stacks/dev-preview` under a state key of its own (`preview/<run id>.tfstate`), waits for
`/ready`, runs `pytest e2e` against the real URLs (browser through gRPC-Web, and native gRPC
over TLS), keeps the preview up for `hold_minutes` (default 10) so you can test by hand, then
destroys the apps, drops the database and deletes the state file. Cancelling the run during the
hold tears it down at once. Run it by hand with `keep` ticked to leave a preview up, and remove
it later with `destroy-dev-preview` (the run id is in the run summary).

The previews run `ENVIRONMENT=test` so the browser dev sign-in used by the tests works. The
dev sign-in code is the `DEV_PREVIEW_LOGIN_CODE` secret, and the preview URLs are public for a few
minutes only, so keep that code long and private.
