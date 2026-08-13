"""Mine the scraped corpus for two things the style index can't give us.

**Vocabulary evidence.** The register table in FinGuru's instructions was
written from my own sense of how Indians talk about money. This counts what
people actually wrote, so each row can be justified by usage rather than by
taste -- and so a pair I got backwards shows up as a number instead of
surviving because nobody checked.

**Real questions.** The Hindi audit currently runs on ten questions I made
up, which tests the agent against my idea of how people ask. The corpus is
full of genuine ones, typos and dialect included. Note the symmetry with
build_style_index.py: the passages that filter *rejects* as questions are
precisely what is wanted here.

Usage:
    python scripts/mine_vernacular.py \
        --corpus "C:/path/to/vernacular_style/data/clean" --lang hi
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Each pair is (spoken, formal). The claim under test is that the left side
# is what people write and the right side is what a model reaches for.
#
# Two traps this list has already fallen into, both of which produced a
# confident and completely wrong "FORMAL wins":
#   - Bare कर counted 413 hits, almost all of them the verb करना ("to do"),
#     not the noun "tax". Short Devanagari words need their compound forms.
#   - प्रीमियम was paired against किस्त, which means instalment. Different
#     word, not a register choice. A pair has to be genuine synonyms or the
#     count means nothing.
REGISTER_PAIRS = [
    ("सेविंग", r"बचत(?!\s*योजना)"),   # बचत योजना is the scheme's real name
    ("अकाउंट", "खाता"),
    ("एफडी|FD", "सावधि जमा|मियादी जमा"),
    ("इंटरेस्ट", "ब्याज"),
    ("सीनियर सिटीजन", "वरिष्ठ नागरिक"),
    ("टैक्स", "आयकर|कर-मुक्त|करमुक्त|कर लाभ|कर छूट"),
    ("लोन", "ऋण"),
    ("मैच्योरिटी|मैच्योर", "परिपक्व"),
    ("बैलेंस", "शेष राशि|शेषफल"),
    ("इन्वेस्ट", "निवेश"),
    ("रिटर्न", "प्रतिफल"),
    ("पॉलिसी", "बीमा पत्र"),
    ("डॉक्यूमेंट", "दस्तावेज|अभिलेख"),
    ("प्रीमियम", "प्रब्याजि"),
]

# Devanagari word, or a Latin run (people write FD, SIP, EMI unchanged).
_TOKEN = re.compile(r"[ऀ-ॿ]+|[A-Za-z]{2,}")

_STOPWORDS = {
    "है", "हैं", "का", "की", "के", "को", "में", "से", "पर", "और", "यह", "वह",
    "नहीं", "भी", "तो", "ही", "कि", "हो", "था", "थी", "मैं", "आप", "कर", "करने",
    "लिए", "एक", "कोई", "क्या", "जो", "अपने", "मेरा", "मेरी", "sir", "the",
    "ka", "hai", "ko", "me", "se", "ki", "kya", "aur", "nahi", "ye", "bhi",
}

_QUESTION = re.compile(r"[?？]|क्या|कैसे|कितन|कौन|कब|कहाँ|कहां|क्यों|चाहिए|बताइए|बताए|बतायें")
_SOLICIT = re.compile(r"\b\d{10}\b|100\s*%|💯|whats?app|telegram|dila|dilate", re.IGNORECASE)
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def devanagari_share(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if _DEVANAGARI.match(c)) / len(letters)


def load(corpus_dir: Path, lang: str) -> list[str]:
    path = corpus_dir / f"{lang}.jsonl"
    if not path.exists():
        raise SystemExit(f"No such corpus file: {path}")
    texts = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                texts.append(json.loads(line).get("text", ""))
            except json.JSONDecodeError:
                continue
    return texts


def report_register(texts: list[str]) -> list[str]:
    blob = "\n".join(texts)
    lines = ["## Register evidence", "",
             "| spoken | count | formal | count | verdict |",
             "|---|---:|---|---:|---|"]
    for spoken, formal in REGISTER_PAIRS:
        s = len(re.findall(spoken, blob))
        f = len(re.findall(formal, blob))
        if s == 0 and f == 0:
            verdict = "no evidence"
        elif s >= max(f * 2, f + 3):
            verdict = "**spoken wins**"
        elif f >= max(s * 2, s + 3):
            verdict = "**FORMAL wins — table may be wrong**"
        else:
            verdict = "too close to call"
        lines.append(f"| {spoken} | {s} | {formal} | {f} | {verdict} |")
    return lines


def report_terms(texts: list[str], top: int = 40) -> list[str]:
    counts: collections.Counter[str] = collections.Counter()
    for text in texts:
        for token in _TOKEN.findall(text):
            low = token.lower()
            if len(token) > 2 and low not in _STOPWORDS:
                counts[token] += 1
    lines = ["", "## Most-used words in the corpus", "",
             "Scan for finance vocabulary the register table is missing.", "", "```"]
    lines += [f"{count:5d}  {term}" for term, count in counts.most_common(top)]
    lines.append("```")
    return lines


def extract_questions(texts: list[str], limit: int) -> list[str]:
    seen, questions = set(), []
    for text in texts:
        stripped = " ".join(text.split())
        if not (25 <= len(stripped) <= 200):
            continue
        if devanagari_share(stripped) < 0.6 or _SOLICIT.search(stripped):
            continue
        if not _QUESTION.search(stripped):
            continue
        key = stripped[:40]
        if key in seen:
            continue
        seen.add(key)
        questions.append(stripped)
        if len(questions) >= limit:
            break
    return questions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--lang", default="hi")
    parser.add_argument("--questions", type=int, default=30)
    parser.add_argument("--out", default=str(REPO_ROOT / "docs" / "vernacular-evidence.md"))
    parser.add_argument("--questions-out",
                        default=str(REPO_ROOT / "tests" / "fixtures" / "hindi_questions.json"))
    args = parser.parse_args()

    texts = load(Path(args.corpus), args.lang)
    print(f"Read {len(texts)} passages from {args.lang}.jsonl")

    report = [f"# Vernacular evidence — {args.lang}", "",
              f"Mined from {len(texts)} scraped passages. Generated by "
              "`scripts/mine_vernacular.py`; do not hand-edit.", ""]
    report += report_register(texts)
    report += report_terms(texts)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(report), encoding="utf-8")
    print(f"Wrote {out}")

    questions = extract_questions(texts, args.questions)
    qout = Path(args.questions_out)
    qout.parent.mkdir(parents=True, exist_ok=True)
    qout.write_text(json.dumps({
        "_note": "Real user questions mined from the scraped corpus, kept "
                 "verbatim -- typos, dialect spellings and all. They test the "
                 "agent against how people actually type, which invented test "
                 "questions cannot.",
        "language": args.lang,
        "questions": questions,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {qout} — {len(questions)} questions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
