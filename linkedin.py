"""Optional Phase 2: publish an approved post to LinkedIn via the Posts API.

Needs a LinkedIn developer app with the "Share on LinkedIn" (w_member_social) and
"Sign In with LinkedIn using OpenID Connect" (openid, profile) products, and a
member access token for Meera's account.
"""

import re

import httpx

from config import settings

# LinkedIn's "little text" format treats these as markup - unescaped, the post gets truncated.
_RESERVED = re.compile(r"([\\|{}@\[\]()<>#*_~])")


def escape_commentary(text: str) -> str:
    return _RESERVED.sub(r"\\\1", text)


class LinkedInError(RuntimeError):
    pass


async def _author_urn(client: httpx.AsyncClient) -> str:
    if settings.linkedin_author_urn:
        return settings.linkedin_author_urn
    resp = await client.get(
        "https://api.linkedin.com/v2/userinfo",
        headers={"Authorization": f"Bearer {settings.linkedin_access_token}"},
    )
    if resp.status_code != 200:
        raise LinkedInError(f"Could not read LinkedIn profile ({resp.status_code}): {resp.text[:300]}")
    return f"urn:li:person:{resp.json()['sub']}"


async def publish(text: str) -> str:
    """Publishes the post and returns its public URL."""
    async with httpx.AsyncClient(timeout=20) as client:
        author = await _author_urn(client)
        resp = await client.post(
            "https://api.linkedin.com/rest/posts",
            headers={
                "Authorization": f"Bearer {settings.linkedin_access_token}",
                "LinkedIn-Version": settings.linkedin_api_version,
                "X-Restli-Protocol-Version": "2.0.0",
                "Content-Type": "application/json",
            },
            json={
                "author": author,
                "commentary": escape_commentary(text),
                "visibility": "PUBLIC",
                "distribution": {
                    "feedDistribution": "MAIN_FEED",
                    "targetEntities": [],
                    "thirdPartyDistributionChannels": [],
                },
                "lifecycleState": "PUBLISHED",
                "isReshareDisabledByAuthor": False,
            },
        )
    if resp.status_code not in (200, 201):
        raise LinkedInError(f"LinkedIn rejected the post ({resp.status_code}): {resp.text[:300]}")
    urn = resp.headers.get("x-restli-id", "")
    return f"https://www.linkedin.com/feed/update/{urn}" if urn else "https://www.linkedin.com/feed/"
