import json
import os
from typing import Dict

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

def configure_cors(app: FastAPI):
    config_str = os.environ.get("CORS_CONFIG")
    if config_str is not None and config_str != "":
        cors_config:Dict = json.loads(config_str)
        allow_origins = cors_config.get("allow_origins") or ["*"]
        allow_credentials = cors_config.get("allow_credentials") or [True]
        allow_headers = cors_config.get("allow_headers") or ["*"]
        allow_methods = cors_config.get("allow_methods") or ["*"]
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allow_origins,
            allow_credentials=allow_credentials,
            allow_methods=allow_methods,
            allow_headers=allow_headers,
        )
