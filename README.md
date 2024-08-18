# dashboard
A Jupyter notebook that creates a dashboard for viewing LizardByte repository data.

## Contributing

1. Notebooks should be committed with the output cleared.
   ```bash
   jupyter nbconvert --to notebook --ClearOutputPreprocessor.enabled=True --inplace ./notebook/dashboard.ipynb
   ```

2. You can create a preview of the notebook in html by running the following commands:
   ```bash
   npm install
   cp -f ./node_modules/ploty.js/dist/plotly.min.js ./gh-pages/plotly.js
   jupyter nbconvert \
     --debug \
     --config=./jupyter_nbconvert_config.py \
     --execute \
     --no-input \
     --to=html \
     --output-dir=./gh-pages \
     --output=index \
     ./notebook/dashboard.ipynb
   ```
