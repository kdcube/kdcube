#!/usr/bin/env python3

# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/viz/patch_plarform_dashboard.py
"""
Patch Platform Dashboard with runtime settings
"""

import argparse
import re
from pathlib import Path
from typing import Optional


def patch_dashboard_f(
        input_file: Path,
        output_file: Path,
        base_url: str,
        default_tenant: str = "home",
        default_project: str = "demo",
        default_app_bundle_id: str = "kdcube.codegen.orchestrator",
        access_token: Optional[str] = None,
        id_token: Optional[str] = None,
        id_token_header: str = "X-ID-Token"
) -> None:
    """
    Patch the OPEX dashboard HTML/TSX with runtime settings.

    Replaces template placeholders:
        {{CHAT_BASE_URL}} -> actual base URL
        {{ACCESS_TOKEN}} -> actual access token (or empty)
        {{ID_TOKEN}} -> actual ID token (or empty)
        {{ID_TOKEN_HEADER}} -> actual header name
        {{DEFAULT_TENANT}} -> default tenant
        {{DEFAULT_PROJECT}} -> default project
        {{DEFAULT_APP_BUNDLE_ID}} -> default app bundle ID
    """

    print(f"Reading from: {input_file}")
    content = input_file.read_text(encoding='utf-8')

    # Perform replacements
    replacements = {
        '{{CHAT_BASE_URL}}': base_url,
        '{{ACCESS_TOKEN}}': access_token or '',
        '{{ID_TOKEN}}': id_token or '',
        '{{ID_TOKEN_HEADER}}': id_token_header,
        '{{DEFAULT_TENANT}}': default_tenant,
        '{{DEFAULT_PROJECT}}': default_project,
        '{{DEFAULT_APP_BUNDLE_ID}}': default_app_bundle_id
    }

    for placeholder, value in replacements.items():
        if placeholder in content:
            content = content.replace(placeholder, value)
            print(f"✓ Replaced {placeholder}")
        else:
            print(f"⚠ Warning: {placeholder} not found in file")

    # Write output
    output_file.write_text(content, encoding='utf-8')
    print(f"\n✓ Patched dashboard written to: {output_file}")
    print(f"\nConfiguration:")
    print(f"  Base URL: {base_url}")
    print(f"  Default Tenant: {default_tenant}")
    print(f"  Default Project: {default_project}")
    print(f"  Default Bundle ID: {default_app_bundle_id}")
    print(f"  Access Token: {'<set>' if access_token else '<not set>'}")
    print(f"  ID Token: {'<set>' if id_token else '<not set>'}")
    print(f"  ID Token Header: {id_token_header}")

def patch_dashboard(
        input_content: str,
        base_url: str,
        default_tenant: str = "home",
        default_project: str = "demo",
        default_app_bundle_id: str = "with.codegen",
        access_token: Optional[str] = None,
        id_token: Optional[str] = None,
        id_token_header: str = "X-ID-Token"
) -> str:
    """
    Patch the OPEX dashboard HTML/TSX with runtime settings.

    Replaces template placeholders:
        {{CHAT_BASE_URL}} -> actual base URL
        {{ACCESS_TOKEN}} -> actual access token (or empty)
        {{ID_TOKEN}} -> actual ID token (or empty)
        {{ID_TOKEN_HEADER}} -> actual header name
    """

    # Perform replacements
    replacements = {
        # '{{CHAT_BASE_URL}}': base_url,
        '{{ACCESS_TOKEN}}': access_token or '',
        '{{ID_TOKEN}}': id_token or '',
        '{{ID_TOKEN_HEADER}}': id_token_header,
        '{{DEFAULT_TENANT}}': default_tenant,
        '{{DEFAULT_PROJECT}}': default_project,
        '{{DEFAULT_APP_BUNDLE_ID}}': default_app_bundle_id
    }

    output_content = input_content
    for placeholder, value in replacements.items():
        if placeholder in output_content:
            output_content = output_content.replace(placeholder, value)
            # print(f"✓ Replaced {placeholder}")
        else:
            print(f"⚠ Warning: {placeholder} not found in file")

    return output_content
