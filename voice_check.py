"""Deterministic checks for the hard rules in the Meera voice skill.

Mechanical issues (dashes, US spellings, exclamation marks, emojis, trailing
hashtags) are fixed automatically. Issues that need rewriting (banned words,
engagement bait, bullets, inline hashtags, length) are returned as violations
so the generator can ask Gemini for a revision.
"""

import re
from dataclasses import dataclass, field

EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF\U0000FE0F\U0000200D]"
)
HASHTAG_RE = re.compile(r"(?<![\w&])#[A-Za-z]\w*")
BULLET_RE = re.compile(r"^\s*(?:[-•*▪►]|\d+[.)])\s+", re.MULTILINE)
PLACEHOLDER_RE = re.compile(r"\[[^\]]{1,40}\]")

BANNED_WORDS = [
    "amazing", "incredible", "revolutionary", "game-changing", "game changer", "game-changer",
    "magical", "ultimate", "must-have", "obsessed", "life-changing", "secret", "hack", "hacks",
    "breakthrough", "holy grail", "glow", "skin-loving", "journey", "passionate",
    "excited to announce", "excited to share", "thrilled", "disrupt", "disrupting", "disruptive",
]
BANNED_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in BANNED_WORDS) + r")\b", re.IGNORECASE)

BAIT_PHRASES = [
    "let me know what you think", "agree?", "thoughts?", "what do you think?", "follow for more",
    "dm me", "link in bio", "comment below", "drop a comment", "like and share", "repost if",
    "excited for what's next",
]

SALUTATIONS = re.compile(r"^\s*(hi|hello|dear|hey)\b[^\n]{0,30}[,\n]", re.IGNORECASE)

US_TO_UK = {
    "color": "colour", "colors": "colours", "colored": "coloured", "behavior": "behaviour",
    "behaviors": "behaviours", "behavioral": "behavioural", "favorite": "favourite",
    "flavor": "flavour", "flavors": "flavours", "center": "centre", "centers": "centres",
    "fiber": "fibre", "fibers": "fibres", "labeled": "labelled", "labeling": "labelling",
    "modeling": "modelling", "traveled": "travelled", "canceled": "cancelled", "defense": "defence",
    "analyze": "analyse", "analyzed": "analysed", "analyzing": "analysing",
}
IZE_STEMS = (
    "optim", "organ", "real", "stabil", "standard", "character", "oxid", "moistur", "sensit",
    "minim", "maxim", "recogn", "priorit", "emphas", "summar", "util", "neutral", "steril",
    "categor", "custom", "special", "ional", "commercial", "normal", "capital", "apolog",
)
IZE_RE = re.compile(r"\b(" + "|".join(IZE_STEMS) + r")iz(e|es|ed|ing|er|ers|ation|ations)\b", re.IGNORECASE)
US_WORD_RE = re.compile(r"\b(" + "|".join(US_TO_UK) + r")\b", re.IGNORECASE)

# Negations only - "It is also not helpful" style emphasis stays, but "was not"/"cannot" read stiff in her register
CONTRACTIONS = {
    "is not": "isn't", "are not": "aren't", "was not": "wasn't", "were not": "weren't",
    "do not": "don't", "does not": "doesn't", "did not": "didn't", "have not": "haven't",
    "has not": "hasn't", "had not": "hadn't", "cannot": "can't", "could not": "couldn't",
    "would not": "wouldn't", "should not": "shouldn't", "will not": "won't", "I am": "I'm",
}
CONTRACTION_RE = re.compile(r"\b(" + "|".join(CONTRACTIONS) + r")\b", re.IGNORECASE)
CONTRACTIONS = {k.lower(): v for k, v in CONTRACTIONS.items()}

FILLER_WORDS = [
    "significantly", "considerably", "substantially", "substantial", "quite", "very", "really", "extremely",
    "simply", "straightforward", "truly", "highly", "basically", "literally", "incredibly", "hugely",
    "genuinely", "noticeably", "remarkably", "deeply",
]
FILLER_RE = re.compile(r"\b(" + "|".join(FILLER_WORDS) + r")\b", re.IGNORECASE)
MAX_FILLER = 1
MISLEADING_RE = re.compile(r"\bmisleading\b", re.IGNORECASE)

MIN_WORDS, MAX_WORDS = 300, 650


@dataclass
class CheckResult:
    text: str
    violations: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    placeholders: list[str] = field(default_factory=list)
    word_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.violations


def _match_case(src: str, repl: str) -> str:
    return repl[0].upper() + repl[1:] if src[:1].isupper() else repl


def autofix(text: str) -> tuple[str, list[str]]:
    fixes = []
    original = text

    # Strip common wrappers Gemini sometimes adds
    text = re.sub(r"^```[a-z]*\n|\n```$", "", text.strip())
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)  # markdown bold

    # Dashes: numeric ranges become "4-6", other dashes become spaced hyphens
    new = re.sub(r"(\d)\s*[–—]\s*(\d)", r"\1-\2", text)
    new = re.sub(r"\s*[—–]\s*", " - ", new)
    if new != text:
        fixes.append("em/en dashes -> spaced hyphens")
        text = new

    # "20 to 30 people" -> "20-30 people" (but not "from 9 to 3", which is a change, not a range)
    new = re.sub(r"(?<!from )\b(\d+(?:\.\d+)?)\s+to\s+(\d+(?:\.\d+)?)\b", r"\1-\2", text)
    if new != text:
        fixes.append("numeric ranges -> hyphenated")
        text = new

    # "between 2% and 5%" -> "2-5%"
    new = re.sub(r"\bbetween (\d+(?:\.\d+)?)% and (\d+(?:\.\d+)?)%", r"\1-\2%", text)
    if new != text:
        fixes.append("numeric ranges -> hyphenated")
        text = new

    new = EMOJI_RE.sub("", text)
    if new != text:
        fixes.append("removed emojis")
        text = new

    # Trailing hashtag-only lines
    lines = text.rstrip().split("\n")
    while lines and lines[-1].strip() and all(t.startswith("#") for t in lines[-1].split()):
        lines.pop()
        fixes.append("removed trailing hashtags")
    text = "\n".join(lines)

    new = re.sub(r"!+", ".", text)
    new = re.sub(r"(?<!\.)\.\.(?!\.)", ".", new)
    if new != text:
        fixes.append("exclamation marks -> full stops")
        text = new

    new = CONTRACTION_RE.sub(lambda m: _match_case(m.group(0), CONTRACTIONS[m.group(0).lower()]), text)
    if new != text:
        fixes.append("stiff negatives -> contractions")
        text = new

    new = US_WORD_RE.sub(lambda m: _match_case(m.group(0), US_TO_UK[m.group(0).lower()]), text)
    new = IZE_RE.sub(lambda m: m.group(1) + "is" + m.group(2), new)
    if new != text:
        fixes.append("US -> British spelling")
        text = new

    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, (fixes if text != original.strip() else [])


def check(text: str) -> CheckResult:
    text, fixes = autofix(text)
    violations = []
    lower = text.lower()

    banned = sorted({m.group(0).lower() for m in BANNED_RE.finditer(text)})
    if banned:
        violations.append(f"banned hype words: {', '.join(banned)}")

    bait = [p for p in BAIT_PHRASES if p in lower]
    if bait:
        violations.append(f"engagement-bait phrasing: {', '.join(bait)}")

    fillers = [m.group(0).lower() for m in FILLER_RE.finditer(text)]
    if len(fillers) > MAX_FILLER:
        violations.append(f"filler intensifiers ({len(fillers)}): {', '.join(sorted(set(fillers)))} - cut or replace with specifics")

    if MISLEADING_RE.search(text):
        violations.append("uses 'misleading' - say what the claim is technically true about and what it leaves out")

    tags = HASHTAG_RE.findall(text)
    if tags:
        violations.append(f"hashtags: {', '.join(tags)}")

    if BULLET_RE.search(text):
        violations.append("bullet points or numbered list - must be prose paragraphs")

    if SALUTATIONS.match(text):
        violations.append("salutation at start - LinkedIn posts have none")

    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    one_liners = sum(1 for p in paragraphs if len(re.findall(r"[.?]\s", p + " ")) <= 1)
    if len(paragraphs) >= 6 and one_liners / len(paragraphs) > 0.5:
        violations.append("too many one-line paragraphs (broetry) - use 3-7 sentence paragraphs")

    words = len(re.findall(r"\b\w[\w'-]*\b", text))
    if words < MIN_WORDS:
        violations.append(f"too short ({words} words) - target 350-600")
    elif words > MAX_WORDS:
        violations.append(f"too long ({words} words) - target 350-600")

    return CheckResult(
        text=text,
        violations=violations,
        fixes=fixes,
        placeholders=PLACEHOLDER_RE.findall(text),
        word_count=words,
    )
