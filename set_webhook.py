"""Points Telegram at your Vercel deployment (run once after the first deploy, or after local polling).

    python set_webhook.py https://your-project.vercel.app
    python set_webhook.py --info
"""

import sys

import httpx

from config import settings

API = f"https://api.telegram.org/bot{settings.telegram_token}"


def info() -> None:
    r = httpx.get(f"{API}/getWebhookInfo", timeout=15).json()["result"]
    print(f"URL: {r.get('url') or '(none - bot is in polling mode)'}")
    print(f"Pending updates: {r.get('pending_update_count')}")
    if r.get("last_error_message"):
        print(f"Last error: {r['last_error_message']}")


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    if sys.argv[1] == "--info":
        return info()
    if not settings.webhook_secret:
        sys.exit("TELEGRAM_WEBHOOK_SECRET is missing in .env")
    url = sys.argv[1].rstrip("/") + "/api/telegram"
    r = httpx.post(f"{API}/setWebhook", timeout=15, data={
        "url": url,
        "secret_token": settings.webhook_secret,
        "allowed_updates": '["message","callback_query"]',
        "drop_pending_updates": "true",
    }).json()
    print("Webhook set." if r.get("ok") else f"Failed: {r}")
    info()


if __name__ == "__main__":
    main()
