"""Generate a draft from the terminal without Telegram - useful for testing the voice.

    python draft_cli.py sample_notes.txt
"""

import asyncio
import sys

from config import settings
from generator import Generator


async def run(path: str) -> None:
    settings.validate(need_telegram=False)
    notes = open(path, encoding="utf-8").read()
    draft = await Generator().create(notes)
    print(f"Topic: {draft.topic}\nQuery: {draft.search_query}")
    print(f"News: {len(draft.news_checked)} checked, {len(draft.news)} relevant")
    for n in draft.news:
        print("  " + n.as_line())
    r = draft.review
    print(f"Revision rounds: {draft.rounds}")
    for k in ("unsupported", "distorted", "omitted", "voice"):
        for x in getattr(r, k):
            print(f"  REMAINING {k}: {x}")
    c = draft.check
    print(f"\nWords: {c.word_count} | Fixes: {c.fixes or '-'} | Violations: {c.violations or '-'} "
          f"| Placeholders: {c.placeholders or '-'}\n")
    print("=" * 70)
    print(draft.text)
    print("=" * 70)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python draft_cli.py <notes.txt>")
    asyncio.run(run(sys.argv[1]))
