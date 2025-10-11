# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# tools/reflection.py

def get_fully_qualified_type(obj):
    t = type(obj)
    return f"{t.__module__}.{t.__qualname__}"

def fully_qualified_typename(obj):
    return obj.__class__.__module__ + "." + obj.__class__.__qualname__ if obj else None