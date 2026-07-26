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
