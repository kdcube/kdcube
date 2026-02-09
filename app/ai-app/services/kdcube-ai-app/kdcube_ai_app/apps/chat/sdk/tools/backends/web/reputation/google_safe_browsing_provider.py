import aiohttp

from reputation.abstract_reputation_provider import AbstractReputationProvider


class GoogleSafeBrowsingReputationProvider(AbstractReputationProvider):
    API_URL = "https://safebrowsing.googleapis.com/v4/threatMatches:find"

    def __init__(self, api_key: str, client_id: str = "url-checker", client_ver: str = "1.0.0"):
        self.api_key = api_key
        self.client_id = client_id
        self.client_ver = client_ver

    async def check_url(self, url: str) -> bool:
        params = {'key': self.api_key}

        payload = {
            "client": {
                "clientId": self.client_id,
                "clientVersion": self.client_ver
            },
            "threatInfo": {
                "threatTypes": [
                    "MALWARE",
                    "SOCIAL_ENGINEERING",
                    "UNWANTED_SOFTWARE",
                    "POTENTIALLY_HARMFUL_APPLICATION"
                ],
                "platformTypes": ["ANY_PLATFORM"],
                "threatEntryTypes": ["URL"],
                "threatEntries": [
                    {"url": url}
                ]
            }
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(self.API_URL, params=params, json=payload) as resp:
                    if resp.status != 200:
                        return True

                    data = await resp.json()

                    if not data or "matches" not in data:
                        return True
                    return False

            except Exception:
                return True