<div align="center">
  <h1 align="center">dashboard</h1>
  <h4 align="center">LizardByte developer dashboard.</h4>
</div>

<div align="center">
  <a href="https://github.com/LizardByte/dashboard/actions/workflows/update-pages.yml?query=branch%3Amaster"><img src="https://img.shields.io/github/actions/workflow/status/lizardbyte/dashboard/update-pages.yml.svg?branch=master&label=build&logo=github&style=for-the-badge" alt="Build"></a>
  <a href="https://codecov.io/gh/LizardByte/dashboard"><img src="https://img.shields.io/codecov/c/gh/LizardByte/dashboard?token=8Kf2iy4coQ&style=for-the-badge&logo=codecov&label=codecov" alt="Codecov"></a>
  <a href="https://sonarcloud.io/project/overview?id=LizardByte_dashboard"><img src="https://img.shields.io/sonar/quality_gate/LizardByte_dashboard?server=https%3A%2F%2Fsonarcloud.io&style=for-the-badge&logo=sonar&label=sonarcloud" alt="SonarCloud"></a>
</div>

A dashboard for viewing LizardByte repository data inside a Jekyll static site.

## Testing

### Python unit tests

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

### JavaScript unit tests

```bash
npm install --ignore-scripts
npm test
```

Both test suites enforce 100% coverage.
