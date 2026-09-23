"""Verifies every key in .env works before you start the bot.

    python check_setup.py
"""

import asyncio

import httpx
from google import genai

from config import settings


async def main() -> None:
    ok = True

    print("Telegram ...", end=" ")
    try:
        r = httpx.get(f"https://api.telegram.org/bot{settings.telegram_token}/getMe", timeout=10).json()
        print(f"OK - @{r['result']['username']}" if r.get("ok") else f"FAILED - {r.get('description')}")
        ok &= bool(r.get("ok"))
    except Exception as e:
        print(f"FAILED - {e}"); ok = False

    print(f"Gemini ({settings.gemini_model}) ...", end=" ")
    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        resp = await client.aio.models.generate_content(model=settings.gemini_model, contents="Reply with the word OK.")
        print(f"OK - replied '{(resp.text or '').strip()[:20]}'")
    except Exception as e:
        ok = False
        print(f"FAILED - {e}")
        try:
            names = [m.name.removeprefix("models/") for m in client.models.list() if "gemini" in m.name]
            print("   Available models on this key:", ", ".join(names[:15]))
            print("   Set GEMINI_MODEL in .env to one of these.")
        except Exception:
            pass

    print("Allowed chat IDs ...", end=" ")
    print(settings.allowed_chat_ids or "EMPTY - start the bot, send /start, then copy the ID it shows into .env")

    print("News (Google News RSS) ...", end=" ")
    from news import fetch_news
    items = await fetch_news("skincare regulation", settings.news_region, 2)
    print(f"OK - {len(items)} headlines" if items else "no results (network blocked?) - bot still works without it")

    print("LinkedIn ...", end=" ")
    if not settings.linkedin_enabled:
        print("not configured - Approve returns copy-paste text (Phase 1)")
    else:
        r = httpx.get("https://api.linkedin.com/v2/userinfo",
                      headers={"Authorization": f"Bearer {settings.linkedin_access_token}"}, timeout=10)
        print(f"OK - {r.json().get('name')}" if r.status_code == 200 else f"FAILED ({r.status_code}) - {r.text[:200]}")
        ok &= r.status_code == 200

    print("\nAll good - run: python bot.py" if ok else "\nFix the FAILED items above, then re-run.")


if __name__ == "__main__":
    asyncio.run(main())
