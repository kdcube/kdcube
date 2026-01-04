# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
from dataclasses import dataclass
# chat/sdk/storage/rn.py

from urllib.parse import quote

def _safe(s: str) -> str:
    # only protect ':' which we use as RN separators
    return (s or "").replace(":", "%3A")

def rn_message(tenant: str, project: str,
               user_id: str,
               conversation_id: str, turn_id: str,
               role: str, message_id: str) -> str:
    # ef:<tenant>:<project>:chatbot:message:<user_id>:<conversation_id>:<turn_id>:<role>:<message_id>
    return f"ef:{tenant}:{project}:chatbot:message:{_safe(user_id)}:{conversation_id}:{turn_id}:{role}:{message_id}"

def rn_file(tenant: str, project: str,
            user_id: str,
            conversation_id: str, turn_id: str,
            role: str, filename: str) -> str:
    # ef:<tenant>:<project>:chatbot:file:<user_id>:<conversation_id>:<turn_id>:<role>:<filename>
    safe = _safe(filename)
    return f"ef:{tenant}:{project}:chatbot:file:{_safe(user_id)}:{conversation_id}:{turn_id}:{role}:{safe}"

def rn_attachment(tenant: str, project: str,
                  user_id: str,
                  conversation_id: str, turn_id: str,
                  role: str, filename: str) -> str:
    # ef:<tenant>:<project>:chatbot:attachment:<user_id>:<conversation_id>:<turn_id>:<role>:<filename>
    safe = _safe(filename)
    return f"ef:{tenant}:{project}:chatbot:attachment:{_safe(user_id)}:{conversation_id}:{turn_id}:{role}:{safe}"

def rn_execution_file(tenant: str, project: str,
                      user_id: str,
                      conversation_id: str, turn_id: str,
                      role: str, kind: str, rel_path: str) -> str:
    # ef:<tenant>:<project>:chatbot:execution:<user_id>:<conversation_id>:<turn_id>:<role>:<kind>:<rel_path>
    safe = _safe(rel_path)
    return f"ef:{tenant}:{project}:chatbot:execution:{_safe(user_id)}:{conversation_id}:{turn_id}:{role}:{kind}:{safe}"

def rn_citable(tenant: str, project: str,
               user_id: str,
               conversation_id: str, turn_id: str,
               role: str, message_id: str) -> str:
    # ef:<tenant>:<project>:chatbot:citable:<user_id>:<conversation_id>:<turn_id>:<role>:<message_id>
    return f"ef:{tenant}:{project}:chatbot:citable:{_safe(user_id)}:{conversation_id}:{turn_id}:{role}:{message_id}"

import re
FILE_PATH_RE = re.compile(
    r'^cb/tenants/(?P<tenant>[^/]+)/projects/(?P<project>[^/]+)/attachments/(?P<role>[^/]+)/'
    r'(?P<user_id>[^/]+)/'
    r'(?P<conversation_id>[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})/'
    r'(?P<turn_id>turn_[^/]+)/(?P<filename>[^/]+)$'
)

def parse_file_path(path: str) -> dict:
    m = FILE_PATH_RE.match(path)
    if not m:
        raise ValueError("Path did not match expected format")
    return m.groupdict()

def build_file_path(d: dict) -> str:
    return "cb/tenants/{tenant}/projects/{project}/attachments/{role}/{user_id}/{conversation_id}/{turn_id}/{filename}".format(**d)

def rn_file_from_file_path(file_rel_path: str):
    places = parse_file_path(file_rel_path)
    return rn_attachment(**places)
