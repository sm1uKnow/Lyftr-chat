import os

# Use LiteLLM's bundled model registry to keep startup independent of GitHub.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "true")
