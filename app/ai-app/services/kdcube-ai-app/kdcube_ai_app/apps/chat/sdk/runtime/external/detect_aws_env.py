# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/isolated/detect_aws_env.py
import os, sys, pathlib
from typing import Dict, Optional

def detect_aws_environment() -> tuple[bool, Optional[Dict[str, str]]]:
    """
    Detect if we're running on AWS infrastructure or local dev.

    Returns:
        (is_aws_infra, credentials_dict)
        - is_aws_infra: True if running on EC2/ECS/Lambda
        - credentials_dict: Dict of AWS env vars for local dev, None for prod
    """
    # Check 1: AWS execution environment markers
    if os.environ.get("AWS_EXECUTION_ENV"):
        # Running in Lambda, ECS, or other AWS managed environment
        return (True, None)

    if os.environ.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"):
        # Running in ECS with task role
        return (True, None)

    # Check 2: Explicitly marked as using instance role
    if os.environ.get("AWS_USE_INSTANCE_ROLE", "").lower() == "true":
        return (True, None)

    # Check 3: Try to reach EC2 IMDS (with short timeout)
    try:
        import requests
        response = requests.get(
            "http://169.254.169.254/latest/meta-data/instance-id",
            timeout=0.1  # Very short timeout
        )
        if response.status_code == 200:
            # We're on EC2
            return (True, None)
    except Exception:
        # Not on EC2 or IMDS not reachable
        pass

    # We're on local dev - read credentials from AWS CLI config
    try:
        credentials = _read_aws_credentials_from_host()
        return (False, credentials)
    except Exception as e:
        # Couldn't read credentials, but still local
        print(f"[docker.exec] Warning: Could not read AWS credentials: {e}", file=sys.stderr)
        return (False, None)

def check_and_apply_cloud_environment(env: Dict[str, str],
                                      log) -> None:

    # Auto-detect environment and get credentials if needed
    is_aws_infra, local_creds = detect_aws_environment()
    if is_aws_infra:
        log.log("[docker.exec] Running on AWS infrastructure, will use instance role via IMDS", level="INFO")
        # Ensure IMDS-related vars are passed
        imds_vars = {
            "AWS_REGION": os.environ.get("AWS_REGION", "eu-west-1"),
            "AWS_DEFAULT_REGION": os.environ.get("AWS_DEFAULT_REGION", "eu-west-1"),
            "NO_PROXY": "169.254.169.254,localhost,127.0.0.1",
            "AWS_EC2_METADATA_DISABLED": "false",
            "AWS_SDK_LOAD_CONFIG": "1",
        }
        for k, v in imds_vars.items():
            if k not in env:
                env[k] = v
    else:
        log.log("[docker.exec] Running on local dev machine", level="INFO")
        if local_creds:
            log.log(f"[docker.exec] Passing AWS credentials to supervisor (profile: {local_creds.get('AWS_PROFILE', 'default')})", level="INFO")
            # Pass credentials as env vars to supervisor
            env.update(local_creds)
        else:
            log.log("[docker.exec] Warning: No AWS credentials found", level="WARNING")

def _read_aws_credentials_from_host() -> Dict[str, str]:
    """
    Read AWS credentials from the host using boto3's credential chain.
    This respects AWS_PROFILE and all standard AWS credential sources.

    Returns:
        Dict with AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, etc.
    """
    try:
        import boto3
        from botocore.exceptions import NoCredentialsError

        # Get current session (respects AWS_PROFILE from environment)
        session = boto3.Session()
        credentials = session.get_credentials()

        if credentials is None:
            raise NoCredentialsError()

        # Get frozen credentials (resolves temporary credentials if needed)
        frozen = credentials.get_frozen_credentials()

        creds = {
            "AWS_ACCESS_KEY_ID": frozen.access_key,
            "AWS_SECRET_ACCESS_KEY": frozen.secret_key,
        }

        # Add session token if present (for temporary credentials)
        if frozen.token:
            creds["AWS_SESSION_TOKEN"] = frozen.token

        # Add region
        region = session.region_name
        if region:
            creds["AWS_REGION"] = region
            creds["AWS_DEFAULT_REGION"] = region

        # Preserve profile if set
        if "AWS_PROFILE" in os.environ:
            creds["AWS_PROFILE"] = os.environ["AWS_PROFILE"]

        return creds

    except ImportError:
        # boto3 not available - try reading credentials file directly
        return _read_aws_credentials_file()


def _read_aws_credentials_file() -> Dict[str, str]:
    """
    Fallback: Read credentials directly from ~/.aws/credentials file.
    This is less robust than boto3 but works if boto3 isn't available.
    """
    import configparser

    aws_dir = pathlib.Path.home() / ".aws"
    credentials_file = aws_dir / "credentials"
    config_file = aws_dir / "config"

    if not credentials_file.exists():
        raise FileNotFoundError(f"AWS credentials file not found: {credentials_file}")

    # Determine which profile to use
    profile = os.environ.get("AWS_PROFILE", "default")

    # Read credentials
    credentials_parser = configparser.ConfigParser()
    credentials_parser.read(credentials_file)

    if profile not in credentials_parser:
        raise ValueError(f"Profile '{profile}' not found in {credentials_file}")

    creds = {
        "AWS_ACCESS_KEY_ID": credentials_parser[profile]["aws_access_key_id"],
        "AWS_SECRET_ACCESS_KEY": credentials_parser[profile]["aws_secret_access_key"],
    }

    # Check for session token (temporary credentials)
    if "aws_session_token" in credentials_parser[profile]:
        creds["AWS_SESSION_TOKEN"] = credentials_parser[profile]["aws_session_token"]

    # Read region from config file
    if config_file.exists():
        config_parser = configparser.ConfigParser()
        config_parser.read(config_file)

        config_section = f"profile {profile}" if profile != "default" else "default"
        if config_section in config_parser and "region" in config_parser[config_section]:
            region = config_parser[config_section]["region"]
            creds["AWS_REGION"] = region
            creds["AWS_DEFAULT_REGION"] = region

    if profile != "default":
        creds["AWS_PROFILE"] = profile

    return creds