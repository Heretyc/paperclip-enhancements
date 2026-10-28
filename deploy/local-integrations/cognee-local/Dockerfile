FROM cognee/cognee-mcp@sha256:dcc1ac6941bd662d74283ec4aa133fd3f0183f53dc064032e44bae61b84ecc26

# Use the stable SDK, the keyless extractor and matching CPU torch libraries.
RUN uv pip install --python /app/.venv/bin/python --no-deps \
      --index-url https://download.pytorch.org/whl/cpu 'torchvision==0.28.0+cpu' \
 && uv pip install --python /app/.venv/bin/python \
      'cognee[gliner]==1.6.5' 'cognee-mcp==0.5.7' 'torch==2.13.0+cpu' \
 && python -c "import importlib.metadata as m; assert m.version('cognee') == '1.6.5'; import gliner2, fastembed"
