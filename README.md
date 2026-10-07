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

The collector refreshes the current UTC day and recent unsettled days, with a three-hour cache and
no automatic retries. Each update also backfills at most seven missing historical days, starting
with the oldest dates in Azure's available 90-day window. Once a complete day has had two full
days of reporting grace and is fetched successfully, it is finalized and never fetched again.
Historical gaps are queried separately, so a backfill request never spans finalized days.
Finalized history is retained indefinitely in the existing `gh-pages` branch, without Azure storage.
Successfully queried days with no numeric samples remain unknown, rather than becoming zero;
partial month and 30-day totals are labeled while history fills in.

The scheduled job runs eight times per day: one metric query per update when caught up, or at
most two while backfilling (at most 496 queries in a 31-day month for one account). Site visitors
read the generated JSON and never query Azure.
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
3. Run the Update workflow or wait for its next scheduled run.

Local collection accepts the same names in the environment or ignored `.env` file.
Collection stays disabled and the section stays hidden until the resource ID is configured.
Only counts, dates, and collection status appear in the dashboard data; credentials and raw Azure
responses are never published. A failed window retains its previous data, is eligible for retry
after three hours, and does not discard other successfully collected windows.

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
