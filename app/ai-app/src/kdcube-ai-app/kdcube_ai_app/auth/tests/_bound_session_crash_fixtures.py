"""Test-only seams for process-interruption qualification.

No bearer is kept in custody: recovery re-signs the stored claims. These seams
only phase the durable store and prove that no custody call is ever made.
"""
from __future__ import annotations

import asyncio

import pytest

SIGNING_SECRET = "unit-session-signing-secret"


class ForbiddenCustody:
    """Any custody I/O fails the test; ``calls`` stays empty when none happened."""

    def __init__(self):
        self.calls = []

    def _forbidden(self, name):
        self.calls.append(name)
        pytest.fail("issuer reached custody " + name)

    async def create(self, **kwargs):
        self._forbidden("create")

    async def get(self, **kwargs):
        self._forbidden("get")

    async def delete(self, **kwargs):
        self._forbidden("delete")


class PhasedStore:
    def __init__(self, store, hook):
        self.store = store
        self.hook = hook

    def __getattr__(self, name):
        return getattr(self.store, name)

    async def reserve_issuance(self, *args, **kwargs):
        original = await self.store.reserve_issuance(*args, **kwargs)
        if self.hook is not None:
            await self.hook("after_reservation")
        return original

    async def activate_reserved(self, *args, **kwargs):
        original = await self.store.activate_reserved(*args, **kwargs)
        if self.hook is not None:
            await self.hook("after_activation")
        return original


async def pause_after_commit(phase, wanted):
    if phase == wanted:
        # Only a phase marker crosses stdout; no bearer or credentials do.
        print(phase, flush=True)
        await asyncio.Event().wait()
