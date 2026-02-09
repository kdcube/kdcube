import asyncio

from dotenv import load_dotenv, find_dotenv
from kdcube_ai_app.infra.service_hub.cache import create_kv_cache

from kdcube_ai_app.apps.chat.sdk.tools.backends.web.egress_policy import NavigationGuard
from kdcube_ai_app.apps.chat.sdk.tools.backends.web.reputation.abstract_reputation_provider import \
    AbstractReputationProvider

load_dotenv(find_dotenv())

class MockReputationProvider(AbstractReputationProvider):
    async def check_url(self, url: str) -> bool:
        await asyncio.sleep(1)
        return "malware" not in url

async def main():
    print("--- SSRF Protection Demo ---")

    cache = create_kv_cache()

    guard = NavigationGuard(MockReputationProvider(), cache)

    test_url = "https://google.com"
    context = {"check_reputation": True}

    print(f"\n--- Test 1 ---")
    v1 = await guard.get_navigation_verdict(test_url, context)
    print(f"Verdict: {v1.reason}")

    print(f"\n--- Test 2 ---")
    v2 = await guard.get_navigation_verdict(test_url, context)
    print(f"Verdict: {v2.reason}")

    print(f"\n--- Key check ---")
    key = guard._generate_cache_key("google.com")
    print(f"Key: {key}")
    in_redis = await cache.get(key)
    print(f"Data in Redis: {in_redis}")

# import ssl
# import socket
# import requests
# from urllib3.connection import HTTPSConnection
# from requests.adapters import HTTPAdapter
# from urllib.parse import urlparse
#
#
# class PinnedHTTPSConnection(HTTPSConnection):
#     def __init__(self, *args, dest_ip: str, server_hostname: str, **kwargs):
#         self.dest_ip = dest_ip
#         self.server_hostname = server_hostname
#         super().__init__(*args, **kwargs)
#
#     def connect(self):
#         sock = socket.create_connection(
#             (self.dest_ip, self.port),
#             self.timeout,
#             self.source_address,
#         )
#
#         context = ssl.create_default_context()
#         self.sock = context.wrap_socket(
#             sock,
#             server_hostname=self.server_hostname,  # ← SNI
#         )
#
# class PinnedHTTPSAdapter(HTTPAdapter):
#     def __init__(self, dest_ip: str, hostname: str, **kwargs):
#         self.dest_ip = dest_ip
#         self.hostname = hostname
#         super().__init__(**kwargs)
#
#     def get_connection(self, url, proxies=None):
#         return PinnedHTTPSConnection(
#             host=self.hostname,
#             port=443,
#             dest_ip=self.dest_ip,
#             server_hostname=self.hostname,
#         )
#
#
#
# async def fetch_with_ip_pinning(url: str, guard: NavigationGuard):
#     verdict = await guard.get_navigation_verdict(
#         url,
#         context={"check_reputation": False}
#     )
#
#     if not verdict.allowed:
#         raise RuntimeError(f"Blocked: {verdict.reason} {verdict.details}")
#
#     hostname = verdict.details["hostname"]
#     pinned_ip = verdict.details["resolved_ips"][0]
#
#     parsed = urlparse(url)
#
#     session = requests.Session()
#     session.mount(
#         f"{parsed.scheme}://{hostname}",
#         PinnedHTTPSAdapter(
#             dest_ip=pinned_ip,
#             hostname=hostname
#         )
#     )
#
#     response = session.get(
#         url,
#         timeout=5,
#         allow_redirects=False
#     )
#
#     response.raise_for_status()
#     return response.json()
#
# async def main2():
#     guard = NavigationGuard(
#         reputation_provider=None,
#         cache=None
#     )
#
#     url = "https://jsonplaceholder.typicode.com/posts/1"
#
#     data = await fetch_with_ip_pinning(url, guard)
#
#     print("✅ SUCCESS")
#     print(data)
#


if __name__ == "__main__":
    asyncio.run(main())


