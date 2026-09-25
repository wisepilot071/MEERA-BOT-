"""Telegram bot: Meera pastes notes -> gets a LinkedIn draft in her voice -> Approve / Edit / Reject.

Runs two ways with the same handlers:
  - locally:   python bot.py            (long polling, handy for testing)
  - on Vercel: main.py                  (Telegram webhook -> build_application().process_update)
All state lives in store.py so it survives between serverless invocations.
"""

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters,
)

import linkedin
import store
from config import BASE_DIR, settings
from generator import Draft, Generator

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

ON_VERCEL = bool(os.getenv("VERCEL"))
DRAFTS_DIR = BASE_DIR / "drafts"  # local audit copies only; Vercel's filesystem is read-only
NOTE_BUFFER_SECONDS = 3  # Telegram splits long pastes into several messages; wait and merge them
TG_LIMIT = 4000
EDIT_WINDOW = 3600  # how long "tap Edit, then type" stays armed


@dataclass
class Session:
    id: str
    chat_id: int
    draft: Draft
    status: str = "pending"  # pending | approved | published | rejected
    message_id: int | None = None  # message holding the buttons

    async def save(self) -> None:
        await store.set(f"session:{self.id}", {
            "id": self.id, "chat_id": self.chat_id, "status": self.status,
            "message_id": self.message_id, "draft": self.draft.to_dict(),
        })
        if not ON_VERCEL:
            DRAFTS_DIR.mkdir(exist_ok=True)
            record = {"saved_at": time.strftime("%Y-%m-%d %H:%M:%S"), "status": self.status, **self.draft.to_dict()}
            (DRAFTS_DIR / f"{self.id}.json").write_text(
                json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
            )

    @classmethod
    async def load(cls, sid: str) -> "Session | None":
        data = await store.get(f"session:{sid}")
        if not data:
            return None
        return cls(id=data["id"], chat_id=data["chat_id"], draft=Draft.from_dict(data["draft"]),
                   status=data["status"], message_id=data.get("message_id"))


_generator: Generator | None = None


def get_generator() -> Generator:
    global _generator
    if _generator is None:
        _generator = Generator()
    return _generator


# ---------- helpers ----------

def is_authorised(update: Update) -> bool:
    return update.effective_chat is not None and update.effective_chat.id in settings.allowed_chat_ids


async def reject_unauthorised(update: Update) -> None:
    chat_id = update.effective_chat.id
    if not settings.allowed_chat_ids:
        user = update.effective_user
        log.info("SETUP: chat ID %s (%s)", chat_id, user.full_name if user else "unknown")
        await update.effective_message.reply_text(
            f"Setup mode. Your chat ID is {chat_id}.\n\n"
            f"Add it to .env as ALLOWED_CHAT_IDS={chat_id} and restart the bot."
        )
    else:
        log.warning("Blocked message from unauthorised chat %s", chat_id)
        await update.effective_message.reply_text("This bot is private.")


@asynccontextmanager
async def typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    async def loop():
        while True:
            await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(4)
    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        task.cancel()


def keyboard(sid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Approve", callback_data=f"approve:{sid}"),
            InlineKeyboardButton("Edit", callback_data=f"edit:{sid}"),
            InlineKeyboardButton("Reject", callback_data=f"reject:{sid}"),
        ],
        [InlineKeyboardButton("Regenerate (fresh angle)", callback_data=f"regen:{sid}")],
    ])


def summary(session: Session, label: str) -> str:
    d, c, r = session.draft, session.draft.check, session.draft.review
    lines = [f"{label} - {c.word_count} words"]
    if d.assessment and d.assessment.available:
        lines.append(f"Notes quality: {d.assessment.score}/10")
    if d.topic:
        lines.append(f"Topic: {d.topic}")

    if not settings.news_enabled:
        pass
    elif d.news:
        lines.append(f"News: {len(d.news_checked)} recent headlines checked, {len(d.news)} relevant (available as context):")
        lines += [f"  • {n.title[:90]} ({n.source}, {n.published})" for n in d.news]
    elif d.news_checked:
        lines.append(f"News: {len(d.news_checked)} recent headlines checked, none relevant - written from your notes only.")
    else:
        lines.append("News: nothing recent found - written from your notes only.")

    checks = []
    if r is not None and not r.available:
        checks.append("fact check unavailable (Gemini busy) - read carefully")
    elif r is not None:
        checks.append("facts match your notes" if r.facts_ok else "fact check has flags (see below)")
    checks.append("voice rules pass" if c.ok else "voice rules have flags (see below)")
    if d.rounds:
        checks.append(f"{d.rounds} self-revision round{'s' if d.rounds > 1 else ''}")
    lines.append("Quality: " + " · ".join(checks))

    if c.fixes:
        lines.append(f"Auto-fixed: {', '.join(c.fixes)}")
    if c.placeholders:
        lines.append(f"Needs your data: {', '.join(dict.fromkeys(c.placeholders))} - tap Edit and give the numbers.")

    flags = c.violations[:]
    if r is not None:
        flags += [f"not in your notes: {x}" for x in r.unsupported]
        flags += [f"changed meaning: {x}" for x in r.distorted]
        flags += [f"left out: {x}" for x in r.omitted]
    if flags:
        lines.append("Please check before approving:")
        lines += [f"  • {f[:220]}" for f in flags[:6]]
    return "\n".join(lines)


async def remove_buttons(context: ContextTypes.DEFAULT_TYPE, session: Session) -> None:
    if session.message_id:
        try:
            await context.bot.edit_message_reply_markup(session.chat_id, session.message_id, reply_markup=None)
        except BadRequest:
            pass


async def send_draft(context: ContextTypes.DEFAULT_TYPE, session: Session, label: str = "Draft ready") -> None:
    await context.bot.send_message(session.chat_id, summary(session, label))
    text = session.draft.text
    chunks = [text[i:i + TG_LIMIT] for i in range(0, len(text), TG_LIMIT)]
    for chunk in chunks[:-1]:
        await context.bot.send_message(session.chat_id, chunk)
    msg = await context.bot.send_message(session.chat_id, chunks[-1], reply_markup=keyboard(session.id))
    session.message_id = msg.message_id
    await session.save()


# ---------- commands ----------

HELP = (
    "Paste your raw notes here - bullet points, half-thoughts, numbers, anything.\n\n"
    "I'll check recent news on the topic, write a LinkedIn post in your voice, and send it back with:\n"
    "Approve - finalise it{publish}\n"
    "Edit - tell me what to change (e.g. 'shorter', 'the return rate was 4.2%', 'drop the news reference')\n"
    "Reject - discard it\n"
    "Regenerate - same notes, a fresh angle\n\n"
    "First I rate your notes out of 10 (specific moment, mechanism, evidence, insight, reader value, integrity). "
    "Notes scoring {min_score}/10 or more go ahead; otherwise I'll tell you what would strengthen them.\n\n"
    "Every draft is fact-checked against your notes and voice-checked before you see it.\n\n"
    "Commands: /cancel stops an edit in progress, /id shows your chat ID."
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorised(update):
        return await reject_unauthorised(update)
    publish = " and publish to LinkedIn" if settings.linkedin_enabled else " (you then paste it into LinkedIn)"
    await update.message.reply_text(HELP.format(publish=publish, min_score=settings.notes_min_score))


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"Your chat ID: {update.effective_chat.id}")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorised(update):
        return await reject_unauthorised(update)
    await store.delete(f"edit:{update.effective_chat.id}")
    await update.message.reply_text("Cancelled. The last draft's buttons still work.")


# ---------- notes and edits ----------

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorised(update):
        return await reject_unauthorised(update)
    chat_id = update.effective_chat.id
    msg_id = update.message.message_id
    text = update.message.text

    if sid := await store.get(f"edit:{chat_id}"):
        await store.delete(f"edit:{chat_id}")
        if session := await Session.load(sid):
            return await handle_edit(context, session, text)

    # Long pastes arrive as several consecutive messages (message IDs are sequential per chat).
    # Each part is parked under its own key; after a short wait, only the handler for the last
    # part (no successor exists) collects the chain backwards and drafts. No shared mutable list,
    # so it's safe across parallel serverless invocations.
    await store.set(f"part:{chat_id}:{msg_id}", text, ttl=600)
    await asyncio.sleep(NOTE_BUFFER_SECONDS)
    if await store.get(f"part:{chat_id}:{msg_id + 1}") is not None:
        return
    parts, i = [], msg_id
    while (part := await store.get(f"part:{chat_id}:{i}")) is not None:
        parts.append(part)
        await store.delete(f"part:{chat_id}:{i}")
        i -= 1
    if parts:
        await handle_notes(context, chat_id, "\n".join(reversed(parts)))


CRITERIA_LABELS = {
    "anchor": "Specific moment or observation", "mechanism": "Why / how it happens",
    "evidence": "Numbers, data or documents", "insight": "A clear, non-obvious point",
    "reader_value": "Something the reader can do", "integrity": "Honest, low-hype, on-brand",
}


def score_breakdown(a) -> str:
    from generator import CRITERIA_MAX
    return "\n".join(f"  • {CRITERIA_LABELS[k]}: {a.criteria.get(k, 0)}/{m}" for k, m in CRITERIA_MAX.items())


def decline_message(a) -> str:
    lines = [
        "Thank you for sharing these notes.",
        "",
        f"I've rated them {a.score}/10 for post-readiness, and I need at least {settings.notes_min_score}/10 to write "
        "a post that meets your standard - so I'm not able to generate one from these just yet. "
        "This isn't about how they're written; rough notes are perfect. It's that some of the raw material is missing.",
        "",
        "How they scored:",
        score_breakdown(a),
    ]
    if a.missing:
        lines += ["", "What would lift them:"] + [f"  • {m}" for m in a.missing]
    lines += ["", "Add a few of these details and send the notes again - I'll happily take another look."]
    return "\n".join(lines)


async def handle_notes(context: ContextTypes.DEFAULT_TYPE, chat_id: int, notes: str) -> None:
    if len(notes.split()) < 8:
        await context.bot.send_message(chat_id, "That's quite short. Paste a bit more - the claim, what you noticed, any numbers.")
        return
    await context.bot.send_message(chat_id, "Got your notes. First I'm checking whether they have enough material for a post...")
    gen = get_generator()
    try:
        async with typing(context, chat_id):
            assessment = await gen.assess(notes)
    except Exception as e:
        log.exception("Assessment failed")
        assessment = None
        log.warning("Assessment error: %s", e)

    if assessment is None or not assessment.available:
        await context.bot.send_message(
            chat_id,
            "Sorry - I couldn't rate these notes just now (the AI service is busy), so I haven't drafted anything. "
            "Please send them again in a minute or two.",
        )
        return

    log.info("Notes scored %d/10 %s", assessment.score, assessment.criteria)
    if not assessment.passes(settings.notes_min_score):
        await context.bot.send_message(chat_id, decline_message(assessment))
        return

    await context.bot.send_message(
        chat_id,
        f"Notes quality: {assessment.score}/10 - good material. Now checking news, drafting, then fact- and "
        "voice-checking - usually 1-2 minutes.",
    )
    try:
        async with typing(context, chat_id):
            draft = await gen.create(notes, assessment)
    except Exception as e:
        log.exception("Draft generation failed")
        await context.bot.send_message(chat_id, f"Drafting failed: {e}\nTry sending the notes again.")
        return
    session = Session(id=uuid.uuid4().hex[:10], chat_id=chat_id, draft=draft)
    await send_draft(context, session)


async def handle_edit(context: ContextTypes.DEFAULT_TYPE, session: Session, instruction: str) -> None:
    await rework(context, session, "Revised draft", get_generator().revise(session.draft, instruction))


async def rework(context: ContextTypes.DEFAULT_TYPE, session: Session, label: str, job) -> None:
    chat_id = session.chat_id
    await remove_buttons(context, session)
    await context.bot.send_message(chat_id, "Working on it - this includes a fresh fact and voice check.")
    try:
        async with typing(context, chat_id):
            await job
    except Exception as e:
        log.exception("%s failed", label)
        await context.bot.send_message(chat_id, f"That failed: {e}\nThe previous draft is unchanged:")
        msg = await context.bot.send_message(chat_id, session.draft.text, reply_markup=keyboard(session.id))
        session.message_id = msg.message_id
        await session.save()
        return
    await send_draft(context, session, label)


# ---------- buttons ----------

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_authorised(update):
        return await query.answer("Not authorised.", show_alert=True)
    action, sid = query.data.split(":", 1)
    session = await Session.load(sid)
    if not session:
        await query.answer("This draft has expired. Send the notes again.", show_alert=True)
        return await query.edit_message_reply_markup(None)
    if session.status != "pending":
        return await query.answer(f"Already {session.status}.")

    if action == "edit":
        await store.set(f"edit:{session.chat_id}", sid, ttl=EDIT_WINDOW)
        await query.answer()
        await context.bot.send_message(
            session.chat_id, "What should change? Reply with your instructions or the missing numbers. (/cancel to stop)"
        )

    elif action == "regen":
        await query.answer("Regenerating...")
        await rework(context, session, "New version", get_generator().regenerate(session.draft))

    elif action == "reject":
        session.status = "rejected"
        await session.save()
        await query.answer("Rejected")
        await query.edit_message_reply_markup(None)
        await context.bot.send_message(session.chat_id, "Discarded. Send new notes whenever you're ready.")

    elif action == "approve":
        if session.draft.check.placeholders:
            return await query.answer(
                "The post still has placeholders like " + session.draft.check.placeholders[0]
                + ". Tap Edit and give the real figures first.", show_alert=True,
            )
        await query.answer()
        if settings.linkedin_enabled:
            await query.edit_message_reply_markup(InlineKeyboardMarkup([[
                InlineKeyboardButton("Publish to LinkedIn now", callback_data=f"publish:{sid}"),
                InlineKeyboardButton("Back", callback_data=f"back:{sid}"),
            ]]))
        else:
            session.status = "approved"
            await session.save()
            await query.edit_message_reply_markup(None)
            await context.bot.send_message(
                session.chat_id, "Approved. The post above is final - copy it and paste into LinkedIn."
            )

    elif action == "back":
        await query.answer()
        await query.edit_message_reply_markup(keyboard(sid))

    elif action == "publish":
        await query.answer("Publishing...")
        await query.edit_message_reply_markup(None)
        try:
            url = await linkedin.publish(session.draft.text)
        except Exception as e:
            log.exception("LinkedIn publish failed")
            await query.edit_message_reply_markup(keyboard(sid))
            return await context.bot.send_message(
                session.chat_id, f"Publishing failed: {e}\nThe draft is unchanged - you can retry or paste it manually."
            )
        session.status = "published"
        await session.save()
        await context.bot.send_message(session.chat_id, f"Published on LinkedIn:\n{url}")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, (NetworkError, TimedOut)):
        log.warning("Telegram connection issue (will retry automatically): %s", err)
    else:
        log.error("Unhandled error", exc_info=err)


def build_application(webhook: bool = False) -> Application:
    settings.validate()
    if not settings.allowed_chat_ids:
        log.warning("ALLOWED_CHAT_IDS is empty - bot runs in setup mode and will only reply with chat IDs.")
    builder = Application.builder().token(settings.telegram_token).concurrent_updates(True)
    if webhook:
        builder = builder.updater(None)  # Telegram pushes updates to us; no polling loop
    app = builder.build()
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    return app


def main() -> None:
    log.info("Model: %s | Reviewer: %s | News: %s | LinkedIn publishing: %s", settings.gemini_model,
             settings.gemini_review_model or settings.gemini_model, settings.news_enabled, settings.linkedin_enabled)
    log.warning("Local polling mode: this REMOVES any Vercel webhook. Re-run set_webhook.py afterwards "
                "to hand the bot back to Vercel.")
    app = build_application()
    log.info("Bot running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
