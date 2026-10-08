<div align="center">
  <img
    src="https://raw.githubusercontent.com/LizardByte/.github/refs/heads/master/branding/logos/logo.svg"
    alt="LizardByte icon"
    width="256"
  />
  <h1 align="center">dashboard</h1>
  <h4 align="center">LizardByte developer dashboard.</h4>
</div>

<div align="center">
  <a href="https://github.com/LizardByte/dashboard/actions/workflows/update-pages.yml?query=branch%3Amaster"><img src="https://img.shields.io/github/actions/workflow/status/lizardbyte/dashboard/update-pages.yml.svg?branch=master&label=build&logo=github&style=for-the-badge" alt="Build"></a>
  <a href="https://codecov.io/gh/LizardByte/dashboard"><img src="https://img.shields.io/endpoint.svg?url=https%3A%2F%2Fapp.lizardbyte.dev%2Fdashboard%2Fshields%2Fcodecov%2Fdashboard.json&style=for-the-badge&logo=codecov" alt="Codecov"></a>
  <a href="https://sonarcloud.io/project/overview?id=LizardByte_dashboard"><img src="https://img.shields.io/sonar/quality_gate/LizardByte_dashboard.svg?server=https%3A%2F%2Fsonarcloud.io&style=for-the-badge&logo=sonarqubecloud&label=sonarcloud" alt="SonarCloud"></a>
</div>

## Overview

A dashboard for viewing LizardByte repository data inside a Jekyll static site.

## Azure code signing metrics

The optional Azure Code Signing section displays Artifact Signing (formerly Trusted Signing)
completed signing requests: UTC month-to-date and last-30-day totals, plus daily history.
Counts cover the entire signing account. Azure's standard `SignCompleted` metric does not include
repository, file, certificate profile, failure-rate, or signing-duration dimensions; these counts
are not billing records. Azure reporting can be delayed and the current day is incomplete.
Missing metric samples are not presented as confirmed zero usage.

### Collection costs

The collector refreshes the current UTC day and recent unsettled days, with a three-hour cache for
successful collections and no automatic retries. Any failed collection can be retried on the next
production or manual workflow run immediately. Each update also backfills at most seven missing
historical days, starting with the oldest dates in Azure's available 90-day window. Once a complete day has had two full
days of reporting grace and is fetched successfully, it is finalized and never fetched again.
Historical gaps are queried separately, so a backfill request never spans finalized days.
Finalized history is retained indefinitely in the existing `gh-pages` branch, without Azure storage.
Successfully queried days with no numeric samples remain unknown, rather than becoming zero;
partial month and 30-day totals are labeled while history fills in.
Queries use `SignCompleted`'s supported one-minute interval in the resource's default metric
namespace, then sum the samples into UTC days locally. Each bounded time window still uses one query.

The scheduled job runs eight times per day: one metric query per update when caught up, or at
most two while backfilling (at most 496 queries in a 31-day month for one account). Site visitors
read the generated JSON and never query Azure.
Pull-request builds use only the Azure data restored from the published `gh-pages` cache and make
no Azure API calls, even when signing secrets are available. Their preview displays cached counts
when available; without published Azure data, the section stays hidden.
No diagnostic settings, Azure Storage, Log Analytics, Event Hubs, custom metrics, or Azure alerts
are created or required.

Microsoft currently lists unlimited standard platform metric ingestion as free and the first
1,000,000 metric query API calls per month as included. This allowance is shared with other consumers
in the Azure subscription; exceeding it can incur charges. With no other monitoring consumers,
this collector's scheduled usage fits comfortably within the included allowance. Your existing
signing plan and any signature overage charges still apply independently of this dashboard.
See [Azure Monitor pricing](https://azure.microsoft.com/en-us/pricing/details/monitor/) and
[Artifact Signing's supported metric](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/supported-metrics/microsoft-codesigning-codesigningaccounts-metrics).

### Setup

1. Set the repository or organization secret `AZURE_SIGNING_RESOURCE_ID` to the full existing account ID:
   `/subscriptions/<subscription-id>/resourceGroups/<resource-group>/providers/Microsoft.CodeSigning/codeSigningAccounts/<account>`.
   In the Azure portal, open the signing account, select **Overview → JSON View**, and copy its **ID**.
   `AZURE_SIGNING_ACCOUNT` supplies only the final account name; it is not the full resource ID.
   `AZURE_SIGNING_CERT_PROFILE` is not needed for these account-wide metrics.
   The resource ID does not grant access by itself, but using a secret keeps the subscription and
   resource identifiers private and enables GitHub Actions log masking.
2. Make the secrets `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, and `AZURE_CLIENT_SECRET` available to this
   repository. These are the same credential names used by the organization's signing workflows.
   The service principal needs read access to metrics on this account; **Monitoring Reader** scoped
   to the signing account supplies that permission. The signing role alone may not allow metric reads.
   In the signing account's **Access control (IAM)**, choose **Add role assignment → Monitoring Reader**.
   Select **User, group, or service principal**, then the app matching `AZURE_CLIENT_ID`.
3. In **Subscriptions → your subscription → Resource providers**, verify that `Microsoft.Insights`
   is **Registered**. If it is **NotRegistered**, select it and choose **Register**, then wait for
   registration to complete. This is an Azure Monitor prerequisite for exploring metrics;
   see [Microsoft's empty-chart troubleshooting guide](https://learn.microsoft.com/en-us/azure/azure-monitor/metrics/metrics-troubleshoot#chart-shows-no-data).
4. Run the Update workflow or wait for its next scheduled run.

Local collection accepts the same names in the environment or ignored `.env` file.
Collection stays disabled and the section stays hidden until the resource ID is configured.
Only counts, dates, and collection status appear in the dashboard data; credentials and raw Azure
responses are never published. A failed window retains its previous data and does not discard other
successfully collected windows. A cache with `status: error` is eligible for immediate retry on the
next production or manual workflow run, even with existing history and a recent `attempted_at`.
Each run still makes at most two metric queries, with no retries within the run. Repeated runs while
the cache has an error status can therefore make additional queries beyond the scheduled monthly estimate.

If the section reports that metrics are unavailable, check the Update workflow's **Cat log** step.
The collector logs when it starts, whether it uses cached data, and the next eligible request time
in UTC. A fresh collection logs each query's time window, how many days have reported counts, and
the saved status and cache size.
Authentication failures report their HTTP status separately from metric request failures.
For a metrics HTTP 403, check the app's **Monitoring Reader** assignment on the signing account;
HTTP 400 indicates a rejected query, and HTTP 404 indicates the resource could not be found.
If requests succeed but all counts remain unknown, compare the account's `SignCompleted` chart in
Azure Monitor with the collected data, and verify the subscription's `Microsoft.Insights` registration.
Failure logs include HTTP status codes, exception types, and fixed categories for recognized query
errors, without raw responses, resource IDs, or credentials. A run within three hours of a successful
collection reuses the cache, even after a role assignment or other configuration change. An error
status logs that it is retrying without the three-hour wait.
For troubleshooting, setting `DASHBOARD_AZURE_SIGNING_DEBUG=true` also logs Azure's structured error
code and message with credentials, resource identifiers, GUIDs, and URLs redacted. This uses the
existing failed response and makes no additional API calls. Remove the setting after diagnosis.
Successful responses also log the number of samples and how many contain total or count fields,
without logging raw samples or dimension values.

## Testing

### Python unit tests

```bash
uv sync --locked
uv run --locked pytest
```

### JavaScript unit tests

```bash
npm ci --ignore-scripts
npm test
```

Both test suites enforce 100% coverage.
