"""Scores note files against the quality gate - use to check the rubric after changing it.

    python calibrate.py prompts/reference_notes/*.txt test_notes/*.txt
"""

import asyncio
import sys

from config import settings
from generator import Generator


async def main(paths: list[str]) -> None:
    settings.validate(need_telegram=False)
    gen = Generator()
    results = await asyncio.gather(*(gen.assess(open(p, encoding="utf-8").read()) for p in paths))
    for p, a in zip(paths, results):
        if not a.available:
            print(f"{p}: UNAVAILABLE")
            continue
        mark = "PASS" if a.passes(settings.notes_min_score) else "FAIL"
        print(f"{a.score:>2}/10 {mark}  {p}  {a.criteria}")
        print(f"        {a.verdict}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
