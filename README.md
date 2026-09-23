# Meera LinkedIn Bot

Meera pastes raw notes into Telegram and gets back a LinkedIn post written in her voice. She approves, edits or rejects it before anything is published.

```
Meera's notes -> Telegram bot -> Gemini: pick the topic and core claim
                              -> Google News RSS: recent headlines (current context)
                              -> Gemini: keep only headlines directly relevant to the claim
                              -> Gemini + Meera voice skill: write the post (GEMINI_MODEL)
                              -> rule checker: auto-fix dashes, spelling, contractions, ranges, emojis...
                              -> AI editor (GEMINI_REVIEW_MODEL): flags unsupported facts, distorted hedges,
                                 omitted points, voice problems -> targeted revision (max 2 rounds)
             <- draft + quality report in Telegram with [Approve] [Edit] [Reject] [Regenerate]
Approve -> final text to paste into LinkedIn (Phase 1)
        -> or publish through the LinkedIn API (Phase 2, if a token is set)
```

## Setup (about 5 minutes)

1. **Get the keys**
   - Telegram: message @BotFather, send `/newbot`, and copy the token.
   - Gemini: create a key at https://aistudio.google.com/apikey
2. **Put the keys in `.env`** (this file is never committed): set `TELEGRAM_BOT_TOKEN` and `GEMINI_API_KEY`.
3. **Check the setup**
   ```
   venv\Scripts\python check_setup.py
   ```
4. **Lock the bot to Meera.** Start the bot, have Meera send `/start`, and it replies with her chat ID. Put that ID in `.env` as `ALLOWED_CHAT_IDS=123456789`, then restart. Until you do, the bot refuses to write anything.
5. **Run the bot**
   ```
   venv\Scripts\python bot.py
   ```

## Using it

- **Paste notes.** Long pastes that Telegram splits into several messages are joined back together automatically.
- **Edit** means reply with an instruction, such as "make it shorter", "the return rate was 4.2%" or "drop the news reference".
- **Placeholders.** If the argument needs a number that isn't in the notes, the post shows a placeholder such as `[X%]`. Approve stays blocked until Edit fills it in.
- **Saved drafts.** Every draft, with its notes, news, versions and status, is saved in `drafts/<id>.json`.

## Improving the voice match

Put 3–6 of Meera's real posts or newsletters in `examples/` as `.txt` files. They are used as style references only.

## Deploy on Vercel (always on)

On Vercel the bot runs as a webhook: Telegram sends each message to `/api/telegram`,
the function answers at once and drafts in the background (`wait_until`, max 300s).
Drafts and edit state live in Vercel's Runtime Cache (14-day expiry).

1. Import this GitHub repo in Vercel (Add New -> Project -> Import). Framework preset: Other.
2. In **Environment Variables**, paste the contents of your local `.env` (Vercel accepts a pasted .env).
   Required: `TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, `TELEGRAM_WEBHOOK_SECRET`, `ALLOWED_CHAT_IDS`.
3. Deploy, then point Telegram at it:
   ```
   venv\Scripts\python set_webhook.py https://<your-project>.vercel.app
   ```
4. Stop any local `python bot.py` - local polling removes the webhook. Check status any time with
   `python set_webhook.py --info`.

## Test without Telegram

```
venv\Scripts\python draft_cli.py sample_notes.txt
venv\Scripts\python -m unittest discover -s tests
```

## Phase 2: auto-publish to LinkedIn

1. Create an app at https://www.linkedin.com/developers/ and add the products **Share on LinkedIn** and **Sign In with LinkedIn using OpenID Connect**.
2. Generate a member access token for Meera's account with the scopes `openid profile w_member_social`. The LinkedIn developer portal's OAuth token tool works for this.
3. Put it in `.env` as `LINKEDIN_ACCESS_TOKEN`. After that, Approve asks you to confirm with **Publish to LinkedIn now**.

The token expires after about 60 days, so it needs renewing.

## Files

| File | Purpose |
|---|---|
| `bot.py` | Telegram handlers, the approve/edit/reject gate, the access lock |
| `generator.py` | Gemini pipeline: understand, write, revise |
| `prompts/meera_voice_skill.md` | The voice skill, used as Gemini's system prompt |
| `news.py` | Google News RSS context |
| `voice_check.py` | Enforces the hard rules: dashes, spelling, emojis, hashtags, banned words, bullets, length |
| `linkedin.py` | Optional publishing through the LinkedIn Posts API |
| `main.py` | Vercel webhook entry point |
| `store.py` | State storage (Vercel Runtime Cache, in-memory locally) |
| `set_webhook.py` | Connects Telegram to the Vercel URL |
