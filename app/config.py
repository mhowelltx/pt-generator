import os

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8192
TEMPERATURE_GENERATE = 0.4
MAX_RETRIES = 3
RETRY_WAIT_SECONDS = 2
DEFAULT_DURATION = 50

# Optional static API key for authenticating POST /api/generate.
# Set API_KEY in the environment to enable key-based access.
# Leave empty to require session auth only.
API_KEY = os.environ.get("API_KEY", "")
