# dashboard
A Jupyter notebook that creates a dashboard for viewing LizardByte repository data.

## Contributing

1. Notebooks should be committed with the output cleared.
   ```bash
   find . -name '*.ipynb' -exec nb-clean clean {} \;
   ```

   Or for a single notebook:
   ```bash
   nb-clean clean ./notebook/dashboard.ipynb
   ```

2. You can create a preview of the notebook in html by running the following commands:
   ```bash
   npm install --ignore-scripts
   cp -f ./node_modules/ploty.js/dist/plotly.min.js ./gh-pages/plotly.js
   jupyter nbconvert --debug --config=./jupyter_nbconvert_config.py --execute --no-input --to=html --output-dir=./gh-pages --output=index ./notebook/dashboard.ipynb
   ```

### Reviewing PRs
Notebook diffs are difficult to read. To make reviewing easier, you can enable the
[Rich Jupyter Notebook Diff](https://github.blog/changelog/2023-03-01-feature-preview-rich-jupyter-notebook-diffs/)
feature in your GitHub account settings.

Local options are also available. See [this](https://www.reviewnb.com/git-jupyter-notebook-ultimate-guide)
for more information.
