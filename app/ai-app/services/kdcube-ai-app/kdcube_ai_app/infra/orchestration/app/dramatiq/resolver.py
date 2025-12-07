import os
# Redis connection settings
REDIS_URL = (
    f"redis://:{os.environ.get('REDIS_PASSWORD','')}"
    f"@{os.environ.get('REDIS_HOST','localhost')}:"
    f"{os.environ.get('REDIS_PORT','6379')}/0"
)

INSTANCE_ID = os.environ.get("INSTANCE_ID", "home-instance-1")
HEARTBEAT_INTERVAL = int(os.environ.get("HEARTBEAT_INTERVAL", 10))  # seconds
TENANT_ID = os.environ.get("TENANT_ID", "home")
PROJECT_ID = os.environ.get("DEFAULT_PROJECT_NAME", "home")