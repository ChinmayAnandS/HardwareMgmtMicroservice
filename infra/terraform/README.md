# Terraform: dev environment and previews

```
modules/hardware_apps/   the two Container Apps (gRPC API + website), reused by every stack
stacks/dev-base/         long-lived: Log Analytics + the dev Container Apps environment
stacks/dev-preview/      one throwaway API + website pair per pipeline run
bootstrap.sh             one-time: state storage, dev resource group, GitHub OIDC identity
```

Prod (`stacks/prod`) is not here yet: it will adopt the existing production environment with
`terraform import`, because the environment's default domain is part of the URL other teams use.

## One-time setup (you run these; they need your Azure login)

1. `az login`, then `bash infra/terraform/bootstrap.sh westus <github-owner>/<repo>`.
   It prints the values for step 3.
2. Create the dev environment once, from your machine:
   ```
   cd infra/terraform/stacks/dev-base
   terraform init -backend-config="resource_group_name=hardware-tfstate" \
     -backend-config="storage_account_name=<TF_STATE_STORAGE_ACCOUNT>" \
     -backend-config="container_name=tfstate" -backend-config="key=dev-base.tfstate" \
     -backend-config="use_azuread_auth=true"
   terraform apply -var subscription_id=<subscription id>
   ```
   Then confirm it is not an express environment (HTTP/2 ingress needs a workload-profiles one):
   `az containerapp env show -n hardware-dev-env -g hardware-dev-rg --query properties.workloadProfiles`
3. In GitHub, create the Environment `dev` and add:
   - variables: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`,
     `TF_STATE_RESOURCE_GROUP`, `TF_STATE_STORAGE_ACCOUNT`, `TF_STATE_CONTAINER`
   - secret `ATLAS_DEV_MONGO_URI`: an SRV URI **without a database name** for a dev-only Atlas
     user that has `readWriteAnyDatabase` and `dbAdminAnyDatabase` (each preview gets its own
     database, `hw_<run id>`). Never reuse the production user or cluster.
4. Atlas dev project: allow `0.0.0.0/0` in the IP access list. GitHub runners and Container
   Apps have no fixed addresses. This is acceptable for a throwaway dev cluster only.
5. Make the two GHCR packages public (`<repo>` and `<repo>-web`), or the apps cannot pull them.

## What a push to `dev` does

`deploy-dev.yml` builds both images and pins them by digest, creates the database, applies
`stacks/dev-preview` under a state key of its own (`preview/<run id>.tfstate`), waits for
`/ready`, runs `pytest e2e` against the real URLs (browser through gRPC-Web, and native gRPC
over TLS), then destroys the apps, drops the database and deletes the state file.
Run it by hand with `keep` ticked to leave a preview up, and remove it later with
`destroy-dev-preview` (the run id is in the run summary).

The previews run `ENVIRONMENT=test` so the browser dev sign-in used by the tests works. The
dev sign-in code is random per run and the URLs are public for a few minutes only.
