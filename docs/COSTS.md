# Hosting and database costs

Reviewed against vendor documentation on 24 October 2026. Prices depend on region, currency, subscription offer, reservation and taxes. Values below are **planning assumptions**, not a quote or measured bill. Recalculate before deployment in the Azure calculator and Atlas cluster builder.

## Hosting comparison

| Choice | Idle behavior | Billing | Fit for this service |
|---|---|---|---|
| Container Apps Consumption | HTTP apps can scale to zero with minReplicas=0; cold starts | Allocated CPU/memory seconds, applicable idle rates, billable requests beyond shared grant | Recommended for low/moderate intermittent administrative traffic |
| App Service paid Linux plan | The hosting plan keeps billing even if the app is stopped; no ordinary scale-to-zero plan | Provisioned VM instances per time; apps can share plan capacity | Can win if an existing adequately sized plan already has spare capacity, or traffic stays steady |
| App Service Free F1 | Shared CPU quota, no production SLA or scale-out | Free within constraints | Development experiments only; do not assume a free production container deployment |

Container Apps includes a monthly subscription-level free grant of **180,000 vCPU-seconds, 360,000 GiB-seconds and 2 million requests**. This is shared by eligible workloads; it is not per app or per environment. Compute is not billed while zero replicas exist. An active or idle running replica can still cost money. Keeping minReplicas=1, running dev and prod together, probes/availability traffic that keep an app active, or oversizing containers can consume the grant.

App Service F1 currently lists **60 CPU minutes/day, 1 GB memory and 1 GB storage** with shared compute constraints. Free/shared plans are not production plans and do not substitute for a paid plan required by a custom-container configuration. ACR has **no permanent free SKU**: Basic/Standard/Premium are paid, with possible account-specific trials/credits. Basic includes 10 GB registry storage; excess storage and networking can cost extra. Building locally/GitHub avoids ACR Tasks build charges. Stopping an application does not delete its ACR, NAT, database or logs.

## Database comparison

| Choice | Cost behavior | Performance / regions | Compatibility and integration |
|---|---|---|---|
| Atlas Free | 512 MB shared storage tier; limited supported regions/features | Useful for learning, shared capacity and limits | Native MongoDB replica-set behavior; no private endpoints; check current free backups/limits rather than assuming they never exist |
| Atlas Flex | Vendor advertises usage-based roughly $8–$30/month; verify cloud/region availability | Shared capacity; intended for development/small workloads | Native MongoDB; inspect tier restrictions, network and backup capabilities |
| Atlas dedicated M10+ | Provisioned cluster hours plus backup/transfer/other usage; does not scale to zero | Three-node replica set; cloud/region choices and dedicated resources | Recommended production baseline here; native cross-collection transactions, custom collection permissions, Atlas backup tooling |
| Cosmos DB MongoDB RU API | Provisioned/autoscale RU/s or serverless consumed RUs + storage; replicated regions add cost | Azure regional availability, latency SLAs; throughput depends on query/index/RU design | Azure billing/identity/monitoring integration; **not a drop-in replacement for this implementation's cross-collection transactions** |

Microsoft documents RU-based MongoDB 4.0 multi-document transactions only within one unsharded collection, with a fixed five-second transaction timeout. This service commits hardware, allocations, checkouts, audit and receipts across collections, so that deployment target is unsupported here. Azure DocumentDB / former MongoDB vCore offerings are separate products with separate pricing and compatibility; they are not silently interchangeable with the RU API. A port would require its own transaction, RBAC and index acceptance tests.

Cosmos DB free tier can cover **1,000 provisioned RU/s and 25 GB**, with one eligible free-tier account per subscription and opt-in requirements. The lifetime free-tier discount is **not available for serverless accounts**. Serverless RU billing can be attractive for light intermittent operations, but a lower cost does not solve a transactional incompatibility. RU spikes, cross-partition queries, missing indexes, geo-replication and backup choices are common surprises.

## Workload model

Assume both apps allocate 0.25 vCPU and 0.5 GiB each. If they each run 100 hours during a month, combined allocation is:

- CPU: `2 * 0.25 * 100 * 3600 = 180,000 vCPU-seconds`.
- Memory: `2 * 0.5 * 100 * 3600 = 360,000 GiB-seconds`.

Those quantities match the free compute grant if no other eligible apps consume it. It does **not** imply that 100 hours of request processing is free in every subscription or plan, nor that the other resources are free. Requests arriving through the proxy can exercise both apps. Idle billing rules, revision overlap and cold starts require real measurement.

For 730 hours each, allocation is 1,314,000 vCPU-seconds and 2,628,000 GiB-seconds. As an illustrative **active-rate assumption only**, use $0.000024/vCPU-second and $0.000003/GiB-second. After an otherwise unused grant, compute is approximately `$27.22 + $6.80 = $34.02/month`. This is arithmetic on assumed rates, not a current regional price quote; apply the actual active/idle prices from the calculator. Request, network and fixed resource charges are additional.

A transparent illustrative intermittent **production baseline**:

| Cost item | Assumption | Illustrative monthly amount |
|---|---|---|
| Container Apps compute/requests | Light traffic within otherwise-unused grants | $0; measure actual eligibility/usage |
| Atlas dedicated cluster | Assume $0.08/hour for 730 hours, verify Azure region | $58.40 before backup/transfer |
| ACR Basic | Assume $5/month | $5 |
| NAT gateway | Assume $0.045/hour for 730 hours | $32.85 plus data processing |
| Static IPv4 | Assume $0.005/hour for 730 hours | $3.65 |
| Logging, backup, secret operations | Example allowance, not a service cap | $10–$25 |
| **Illustrative total** | Excludes tax, transfer, alerts and overages | **About $110–$125/month** |

This fixed network/database floor is real even when application replicas reach zero. The provided production scripts choose a stable NAT IP and restricted Atlas access rather than allowing the database from every IP. NAT is not technically required for Container Apps; it is included for this access-control design. If your organization already provides controlled egress, reuse it with appropriate network configuration and remove duplicate NAT resources. Atlas private endpoints are another design option with their own tier and Azure costs; they are not provisioned by these scripts.

For a student project, prefer local Compose and deploy cloud environments only for demonstrations. Cloud dev in the supplied runbook deliberately uses the production topology and therefore roughly duplicates its fixed baseline. Atlas Free/Flex can reduce database spend for non-production if the required region/features and tests pass, but they do not remove NAT/ACR costs. Do not disable DB access controls merely to reduce the estimate.

The recommendation concerns the requested intermittent compute model. Compare the entire bill: a small paid App Service plan with an acceptable documented outbound-IP allow-list can cost less than a new Container Apps NAT topology, and an existing shared plan can be cheaper still. If a fixed egress IP across infrastructure changes is required, include the corresponding network controls in both estimates. Do not assume scale-to-zero alone identifies the cheapest complete system.

## Spend controls

Keep API, UI, ACR and Atlas in the same Azure region when available. Check actual Central US/East US/West Europe calculator prices and data residency before choosing; a cheaper compute region can lose its advantage through cross-region database latency and transfer charges. Avoid multi-region replicas, premium ingress, private endpoints and dedicated workload profiles until justified.

Keep minimum replicas at zero and maximum at three; size from measured memory and latency. Increase min replicas only for a documented latency requirement. Set resource-group budgets, review Atlas billing separately, cap log ingestion, sample telemetry, avoid secrets in logs, and review alert costs. Keep old registry images needed for rollback but remove unneeded SHA tags/digests to control storage. Use GitHub builds, not ACR Tasks, for the normal pipeline. Check GitHub Actions minute/artifact quotas for private repositories.

Audit and idempotency receipts grow indefinitely, as designed for immutable history and safe retries. Budget their database storage and backups; do not TTL-delete receipts unless you change and document the duplicate-retry contract. Outbound bytes, NAT processing, backups, additional Atlas regions, registry transfer, Azure Monitor retention and secret operations may continue billing independently of request volume.

## Vendor references

- [Container Apps pricing and grants](https://azure.microsoft.com/en-us/pricing/details/container-apps/)
- [Container Apps billing rules](https://learn.microsoft.com/en-us/azure/container-apps/billing)
- [App Service hosting plans](https://learn.microsoft.com/en-us/azure/app-service/overview-hosting-plans)
- [App Service pricing](https://azure.microsoft.com/en-us/pricing/details/app-service/linux/)
- [ACR pricing](https://azure.microsoft.com/en-us/pricing/details/container-registry/)
- [Atlas pricing](https://www.mongodb.com/pricing)
- [Atlas Free cluster limits](https://www.mongodb.com/docs/atlas/reference/free-shared-limitations/)
- [Atlas Flex cost details](https://www.mongodb.com/docs/atlas/billing/atlas-flex-costs/)
- [Cosmos MongoDB transaction limits](https://learn.microsoft.com/en-us/azure/cosmos-db/mongodb/use-multi-document-transactions)
- [Cosmos free-tier rules](https://learn.microsoft.com/en-us/azure/cosmos-db/free-tier)
- [Azure pricing calculator](https://azure.microsoft.com/en-us/pricing/calculator/)
