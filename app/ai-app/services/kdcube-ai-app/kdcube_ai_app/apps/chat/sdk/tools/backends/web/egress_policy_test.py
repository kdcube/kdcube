import unittest
from unittest.mock import patch, AsyncMock
import socket
from egress_policy import NavigationGuard, ReasonCode
from reputation.abstract_reputation_provider import AbstractReputationProvider


class TestNavigationGuard(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.mock_cache = AsyncMock()
        self.mock_cache.get_json.return_value = None
        self.guard = NavigationGuard(cache=self.mock_cache)

    async def test_ip_literals_blocked(self):
        # Loopback
        verdict = await self.guard.get_navigation_verdict("http://127.0.0.1")
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, ReasonCode.BLOCKED_LOCALHOST)

        # Private
        verdict = await self.guard.get_navigation_verdict("http://10.0.0.1")
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, ReasonCode.BLOCKED_PRIVATE_IP)

        # Link-local
        verdict = await self.guard.get_navigation_verdict("http://169.254.1.1")
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, ReasonCode.BLOCKED_LINK_LOCAL)

    async def test_ip_literals_allowed(self):
        # Public
        verdict = await self.guard.get_navigation_verdict("http://8.8.8.8")
        self.assertTrue(verdict.allowed)
        self.assertEqual(verdict.reason, ReasonCode.ALLOWED)

    async def test_blocked_hostnames(self):
        verdict = await self.guard.get_navigation_verdict("http://localhost")
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, ReasonCode.BLOCKED_LOCALHOST)

        verdict = await self.guard.get_navigation_verdict("http://metadata.google.internal")
        self.assertFalse(verdict.allowed)

    async def test_extra_blocked_hostnames(self):
        context = {'blocked_hostnames': ['forbidden.com', 'bad-site.org']}

        verdict = await self.guard.get_navigation_verdict("http://forbidden.com", context)
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, ReasonCode.BLOCKED_LOCALHOST)

        with patch('asyncio.get_running_loop') as mock_loop:
            mock_dns = AsyncMock()
            mock_loop.return_value.getaddrinfo = mock_dns
            mock_dns.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 80))
            ]
            verdict = await self.guard.get_navigation_verdict("http://good-site.com", context)
            self.assertTrue(verdict.allowed)

    async def test_allowlist(self):
        context = {'allowlist': ['example.com']}
        with patch('asyncio.get_running_loop') as mock_loop:
            mock_dns = AsyncMock()
            mock_loop.return_value.getaddrinfo = mock_dns
            mock_dns.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 80))
            ]
            verdict = await self.guard.get_navigation_verdict("http://example.com", context)
            self.assertTrue(verdict.allowed)

        context = {'allowlist': ['example.com']}
        verdict = await self.guard.get_navigation_verdict("http://google.com", context)
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, ReasonCode.BLOCKED_NOT_IN_ALLOWLIST)

    async def test_dns_resolution_allowed(self):
        with patch('asyncio.get_running_loop') as mock_loop:
            mock_dns = AsyncMock()
            mock_loop.return_value.getaddrinfo = mock_dns
            mock_dns.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 80))
            ]

            verdict = await self.guard.get_navigation_verdict("http://google.com")
            self.assertTrue(verdict.allowed)
            self.assertEqual(verdict.reason, ReasonCode.ALLOWED)
            self.mock_cache.set_json.assert_called()

    async def test_dns_resolution_blocked(self):
        with patch('asyncio.get_running_loop') as mock_loop:
            mock_dns = AsyncMock()
            mock_loop.return_value.getaddrinfo = mock_dns
            mock_dns.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 80))
            ]

            verdict = await self.guard.get_navigation_verdict("http://malicious.com")
            self.assertFalse(verdict.allowed)
            self.assertEqual(verdict.reason, ReasonCode.BLOCKED_LOCALHOST)

    async def test_cache_hit_returns_verdict_immediately(self):
        cached_verdict_data = {
            "allowed": True,
            "reason": ReasonCode.ALLOWED,
            "details": {"source": "cache"}
        }
        self.mock_cache.get_json.return_value = cached_verdict_data

        verdict = await self.guard.get_navigation_verdict("http://google.com")

        self.assertTrue(verdict.allowed)
        self.assertEqual(verdict.details["source"], "cache")
        self.mock_cache.get_json.assert_called_once()

    async def test_cache_save_on_success(self):
        with patch('asyncio.get_running_loop') as mock_loop:
            mock_dns = AsyncMock()
            mock_loop.return_value.getaddrinfo = mock_dns
            mock_dns.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 80))
            ]

            await self.guard.get_navigation_verdict("http://google.com")

            self.mock_cache.set_json.assert_called_once()

            args, _ = self.mock_cache.set_json.call_args
            key, data = args
            self.assertIn("nav_guard_egress", key)
            self.assertEqual(data["allowed"], True)
            self.assertEqual(data["reason"], ReasonCode.ALLOWED.value)

    async def test_reputation_check_cached(self):
        mock_provider = AsyncMock(spec=AbstractReputationProvider)
        guard = NavigationGuard(reputation_provider=mock_provider, cache=self.mock_cache)

        mock_provider.check_url.return_value = False

        with patch('asyncio.get_running_loop') as mock_loop:
            mock_dns = AsyncMock()
            mock_loop.return_value.getaddrinfo = mock_dns
            mock_dns.return_value = [(socket.AF_INET, 6, 6, '', ('8.8.8.8', 80))]

            verdict = await guard.get_navigation_verdict(
                "http://phishing.com",
                context={'check_reputation': True}
            )

            self.assertFalse(verdict.allowed)
            self.assertEqual(verdict.reason, ReasonCode.BLOCKED_UNSAFE_REPUTATION)

            self.mock_cache.set_json.assert_called_once()
            _, data = self.mock_cache.set_json.call_args[0]
            self.assertEqual(data['reason'], ReasonCode.BLOCKED_UNSAFE_REPUTATION.value)


if __name__ == '__main__':
    unittest.main()