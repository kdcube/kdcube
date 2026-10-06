# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Local file URI decoding shared by storage and secrets providers."""

from urllib.parse import unquote, urlparse


def local_file_uri_path(uri: str) -> str:
    """Decode a file URI's path once; raw paths and object keys are not URIs.

    Encode a physical Path with Path.as_uri() before passing it through a
    second URI boundary. In particular, a literal ``%40`` must stay literal.
    File authorities retain the existing local-backend handling: only the
    path component is used; this helper does not implement remote filesystems.
    """
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError("Expected a local file URI")
    return unquote(parsed.path)
