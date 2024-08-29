# jupyter imports
from traitlets.config import get_config

# define the config object
c = get_config()

c.Templateexporter.exclude_input_prompt = True
c.HTMLExporter.theme = 'dark'
