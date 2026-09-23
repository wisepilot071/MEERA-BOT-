"""Gemini pipeline.

notes -> understand (topic, claim, key points) -> news -> relevance filter
      -> write -> [rule check + AI review (facts, omissions, voice) -> revise] x N -> final
"""

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

import voice_check
from config import BASE_DIR, settings
from news import NewsItem, fetch_news

log = logging.getLogger(__name__)

SKILL_TEXT = (BASE_DIR / "prompts" / "meera_voice_skill.md").read_text(encoding="utf-8")
MAX_REVISION_ROUNDS = 2


def _load_examples() -> str:
    files = sorted((BASE_DIR / "examples").glob("*.txt"))
    samples = [s for s in (f.read_text(encoding="utf-8").strip() for f in files) if s]
    if not samples:
        return ""
    return (
        "\n\n======================================================================\n"
        "REFERENCE SAMPLES OF MEERA'S PUBLISHED WRITING\n"
        "======================================================================\n"
        "Study these for rhythm, reasoning and restraint ONLY. Never copy their sentences, "
        "facts, numbers, examples or product claims into new posts.\n\n" + "\n\n---\n\n".join(samples[:6])
    )


BOT_RULES = """
======================================================================
OPERATING RULES FOR THIS AUTOMATION (add to everything above)
======================================================================
Source discipline
- The ONLY source of facts, numbers, anecdotes, quotes, dates and company details is SOURCE MATERIAL (Meera's notes plus
  any information she added in edits). Never add a fact, figure, study, motive or detail she did not give.
- Keep the strength of her claims exactly as she stated them: "usually" stays "usually", "maybe 20-30" stays approximate,
  "apparently" stays reported, not confirmed. Do not upgrade hedges into certainty or generalise one case into "the industry".
- You may explain a well-established mechanism in plain terms to connect her points, but add no new numbers or specifics.
- Do not describe what other people think, feel or intend beyond what the notes say.
- If a number the argument needs is missing, insert a placeholder like [X%] rather than inventing one.
- Every substantive point in the notes should appear in the post unless it genuinely does not serve the argument.
  Pay special attention to concessions ("it's legal", "it's true") - they are central to her voice.

Current context
- Headlines are optional. Use one only if it directly supports the core claim, refer to it generically
  ("a report this month on...") and claim nothing beyond the headline. Otherwise ignore them entirely.

Style
- Contractions are normal (it's, doesn't, I'm, we've, isn't). Avoid stiff "it is / I have / do not" where a person would contract.
- Cut filler intensifiers (significantly, considerably, substantial, quite, very, really, extremely, simply, straightforward).
  Replace with a number or delete.
- Ranges as numerals with a hyphen: 20-30 people, 4-6 months.
- Verdicts are plain and firm, not hedged with "feels" or "seems" when the reasoning supports a statement.
- Never call something "misleading"; say what it is technically true about and what it leaves out.

Output
- ONLY the finished post text. No title, preface, explanation, markdown or surrounding quotes.
- Plain prose paragraphs separated by one blank line.
"""


def system_instruction() -> str:
    return SKILL_TEXT + _load_examples() + BOT_RULES


REVIEW_PROMPT = """You are a strict editor checking a LinkedIn draft ghostwritten for Meera Pillai against (a) her source
material and (b) her voice profile (given in your system instructions). Be precise and literal. Quote exact phrases.

SOURCE MATERIAL:
{source}

MEERA'S EDIT REQUESTS SO FAR (respect these - do not flag omissions or changes she asked for):
{edits}

DRAFT:
{draft}

Flag only MATERIAL problems a careful reader or Meera herself would object to. Do NOT flag: harmless rewording,
reordering, time references softened for later publication ("today" -> "recently"), connective reasoning that follows
directly from her points, or the skill's standard framing devices.

Return JSON with exactly these keys (each a list of short strings; empty list if nothing):
"unsupported": facts, numbers, studies, motives, generalisations or details in the draft that are NOT in the source
   material. Any threshold, dose-response, causal or comparative claim ("benefit flattens above X", "risk goes up
   with Y", "most brands do Z") counts as unsupported unless the source states it - even if it is plausibly true.
   Only plain restatement of a mechanism the source itself names is allowed. Format: "<quoted phrase> - why".
"distorted": places where the draft changes the strength or meaning of a source point (hedge upgraded to certainty,
   "mostly" -> "almost all", "usually" -> "always", one case generalised, number altered, stance shifted).
   Format: "<quoted phrase> - source said: ...".
"omitted": substantive source points missing from the draft that the argument needs. Format: "<point>".
"voice": concrete breaches of the voice profile - hype, filler intensifiers, stiff un-contracted phrasing, clunky or
   vague sentences, weak hedged verdicts, broetry, missing non-claim, ending that is a sign-off or pitch, or an
   ending that leaves the reader with nothing concrete to ask, check or remember (Final Voice Check question 10).
   Format: "<quoted phrase> - fix". Only real problems, max 6.
"""


@dataclass
class Review:
    unsupported: list[str] = field(default_factory=list)
    distorted: list[str] = field(default_factory=list)
    omitted: list[str] = field(default_factory=list)
    voice: list[str] = field(default_factory=list)
    available: bool = True  # False if the reviewer call failed - never report "facts OK" in that case

    @property
    def issues(self) -> list[str]:
        return (
            [f"UNSUPPORTED (remove or soften): {x}" for x in self.unsupported]
            + [f"DISTORTED (restore source meaning): {x}" for x in self.distorted]
            + [f"OMITTED (work in naturally): {x}" for x in self.omitted]
            + [f"VOICE: {x}" for x in self.voice]
        )

    @property
    def facts_ok(self) -> bool:
        return self.available and not (self.unsupported or self.distorted)


@dataclass
class Draft:
    notes: str
    topic: str = ""
    core_claim: str = ""
    search_query: str = ""
    news_checked: list[NewsItem] = field(default_factory=list)
    news: list[NewsItem] = field(default_factory=list)  # relevant subset passed to the writer
    edits: list[str] = field(default_factory=list)
    text: str = ""
    check: voice_check.CheckResult | None = None
    review: Review | None = None
    rounds: int = 0
    history: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Draft":
        d = dict(d)
        d["news_checked"] = [NewsItem(**n) for n in d.get("news_checked", [])]
        d["news"] = [NewsItem(**n) for n in d.get("news", [])]
        d["check"] = voice_check.CheckResult(**d["check"]) if d.get("check") else None
        d["review"] = Review(**d["review"]) if d.get("review") else None
        return cls(**d)

    @property
    def source(self) -> str:
        return self.notes + (
            "\n\nADDITIONAL INFORMATION MEERA GAVE IN EDITS:\n" + "\n".join(f"- {e}" for e in self.edits)
            if self.edits else ""
        )


class Generator:
    def __init__(self) -> None:
        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.model = settings.gemini_model
        self.review_model = settings.gemini_review_model or settings.gemini_model

    async def _generate(self, contents: str, system: str | None = None, json_mode: bool = False,
                        temperature: float = 0.6, model: str | None = None) -> str:
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            response_mime_type="application/json" if json_mode else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        # Gemini returns 429/5xx under load: back off, then fall back to a second model
        models = [model or self.model] + ([settings.gemini_fallback_model] if settings.gemini_fallback_model else [])
        last_err: Exception | None = None
        for m in models:
            for attempt in range(3):
                try:
                    resp = await self.client.aio.models.generate_content(model=m, contents=contents, config=config)
                    text = (resp.text or "").strip()
                    if not text:
                        raise RuntimeError("Gemini returned an empty response (possibly blocked by safety filters).")
                    return text
                except genai_errors.APIError as e:
                    last_err = e
                    if e.code not in (429, 500, 502, 503, 504):
                        raise
                    wait = 2 * 2 ** attempt
                    log.warning("Gemini %s on %s (attempt %d) - retrying in %ds", e.code, m, attempt + 1, wait)
                    await asyncio.sleep(wait)
            log.warning("Giving up on %s, trying fallback model", m)
        raise RuntimeError(f"Gemini is unavailable right now ({last_err}). Please try again in a few minutes.")

    async def _json(self, prompt: str, system: str | None = None, model: str | None = None) -> dict:
        try:
            data = json.loads(await self._generate(prompt, system=system, json_mode=True, temperature=0.1, model=model))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, RuntimeError) as e:
            log.warning("JSON call failed: %s", e)
            return {}

    # ---------- steps ----------

    async def understand(self, draft: Draft) -> None:
        data = await self._json(
            "Read these raw notes from a founder. Return JSON with keys:\n"
            '  "topic": neutral 3-8 word descriptive label (no judgement words like misleading, scam, truth),\n'
            '  "core_claim": one sentence - the central observation the post should examine, in her framing,\n'
            '  "search_query": 2-5 keyword Google News query for recent news on this topic (generic terms, no names).\n\n'
            f"NOTES:\n{draft.notes}"
        )
        draft.topic = str(data.get("topic", ""))
        draft.core_claim = str(data.get("core_claim", ""))
        draft.search_query = str(data.get("search_query", ""))

    async def gather_context(self, draft: Draft) -> None:
        if not (settings.news_enabled and draft.search_query):
            return
        draft.news_checked = await fetch_news(
            draft.search_query, settings.news_region, settings.news_max_items, settings.news_max_age_days
        )
        if not draft.news_checked:
            return
        listing = "\n".join(f"{i}. {n.title} ({n.source})" for i, n in enumerate(draft.news_checked))
        data = await self._json(
            f"CORE CLAIM OF A LINKEDIN POST: {draft.core_claim}\n\nHEADLINES:\n{listing}\n\n"
            'Return JSON {"relevant": [indices]} listing ONLY headlines that are directly about the same specific issue '
            "and would add genuine current context. Generic listicles, product roundups and tangential topics are NOT "
            "relevant. Most of the time the answer is an empty list."
        )
        idx = [i for i in data.get("relevant", []) if isinstance(i, int) and 0 <= i < len(draft.news_checked)]
        draft.news = [draft.news_checked[i] for i in idx[:3]]

    def _write_prompt(self, draft: Draft, variation: str = "") -> str:
        news_block = "\n".join(n.as_line() for n in draft.news) if draft.news else "(none relevant - do not reference news)"
        return (
            f"SOURCE MATERIAL (Meera's raw notes):\n{draft.source}\n\n"
            f"CORE CLAIM TO EXAMINE: {draft.core_claim or 'infer from notes'}\n\n"
            f"CURRENT CONTEXT - recent headlines:\n{news_block}\n\n"
            f"{variation}"
            "Before writing, silently list every substantive point and concession in the notes so none is lost. "
            "Then write the LinkedIn post. Run the Final Voice Check silently before answering."
        )

    async def review(self, draft: Draft) -> Review:
        data = await self._json(
            REVIEW_PROMPT.format(
                source=draft.source,
                edits="\n".join(f"- {e}" for e in draft.edits) or "(none)",
                draft=draft.text,
            ),
            system=SKILL_TEXT,
            model=self.review_model,
        )
        if not data:
            return Review(available=False)
        as_list = lambda k: [str(x) for x in data.get(k, []) if str(x).strip()][:8]  # noqa: E731
        return Review(as_list("unsupported"), as_list("distorted"), as_list("omitted"), as_list("voice"))

    async def _polish(self, draft: Draft) -> None:
        """Rule check + AI review, revise until clean or out of rounds, then a final review for reporting."""
        draft.rounds = 0
        while True:
            draft.check = voice_check.check(draft.text)
            draft.text = draft.check.text
            draft.review = await self.review(draft)
            issues = draft.check.violations + draft.review.issues
            if not issues or draft.rounds >= MAX_REVISION_ROUNDS:
                break
            draft.rounds += 1
            log.info("Revision round %d: %d issues", draft.rounds, len(issues))
            try:
                revised = await self._generate(
                    f"SOURCE MATERIAL:\n{draft.source}\n\n"
                    f"MEERA'S EDIT REQUESTS (still apply): {'; '.join(draft.edits) or '(none)'}\n\n"
                    f"CURRENT DRAFT:\n{draft.text}\n\n"
                    "An editor found these problems:\n- " + "\n- ".join(issues) +
                    "\n\nFix these with targeted edits:\n"
                    "1. REPLACE each flagged sentence with its corrected version - the original sentence must be gone, "
                    "never keep both. For unsupported content, delete it or restate only what the source says; never "
                    "swap in a different unsupported detail.\n"
                    "2. For an omitted point, add it once, where it fits the reasoning.\n"
                    "3. Leave unflagged paragraphs as they are.\n"
                    "4. Finally read the whole post once: if any point is now made twice, keep the stronger sentence "
                    "and delete the other.\n"
                    "Stay 350-600 words. Output the full revised post only.",
                    system=system_instruction(),
                    temperature=0.4,
                )
            except RuntimeError as e:
                # Keep the last good draft and its (honest) review rather than failing the whole request
                log.warning("Revision round failed, keeping previous draft: %s", e)
                break
            draft.text = revised

    # ---------- public ----------

    async def create(self, notes: str) -> Draft:
        draft = Draft(notes=notes.strip())
        await self.understand(draft)
        await self.gather_context(draft)
        draft.text = await self._generate(self._write_prompt(draft), system=system_instruction())
        await self._polish(draft)
        return draft

    async def regenerate(self, draft: Draft) -> Draft:
        draft.history.append(draft.text)
        variation = (
            f"A previous version opened like this - take a clearly different opening and structure:\n"
            f"\"{draft.text[:200]}...\"\n\n"
        )
        draft.text = await self._generate(self._write_prompt(draft, variation), system=system_instruction(),
                                          temperature=0.8)
        await self._polish(draft)
        return draft

    async def revise(self, draft: Draft, instruction: str) -> Draft:
        draft.history.append(draft.text)
        draft.edits.append(instruction.strip())
        draft.text = await self._generate(
            f"SOURCE MATERIAL:\n{draft.source}\n\n"
            f"CURRENT DRAFT:\n{draft.text}\n\n"
            f"MEERA'S EDIT REQUEST:\n{instruction.strip()}\n\n"
            "Apply the requested change. Keep everything she did not ask to change. "
            "If she supplies data, use it to replace the matching placeholder. "
            "Stay within every voice rule. Output only the revised post.",
            system=system_instruction(),
            temperature=0.4,
        )
        await self._polish(draft)
        return draft
