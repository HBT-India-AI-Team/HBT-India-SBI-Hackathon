"""Measure the style index's relevance floor instead of guessing it.

MIN_SCORE shipped as a placeholder because there was no corpus to calibrate
against. This is the same method used for doc_search: score a set of finance
questions the corpus should answer in register, score a set of questions it
has no business matching, and read the gap between them.

The floor matters differently here than in doc_search. There, a bad match
produces a wrong citation. Here it produces a passage in the wrong register,
and the model matches its voice to something unrelated -- a gold-loan
walkthrough answering a question about insurance. Retrieving nothing is
strictly better, because nothing means "write as you already would".

Usage:
    OLLAMA_HOST=... python scripts/eval_style_examples.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

# The Windows console is cp1252 and dies on Devanagari, which is every query
# this script prints.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from capabilities_impl import style_examples  # noqa: E402

# Finance questions in Hindi. The corpus is finance creators explaining
# exactly this territory, so these should match well.
HI_ON_TOPIC = [
    "मेरी बेटी 5 साल की है, उसके लिए कौन सी सरकारी योजना अच्छी है?",
    "1 लाख रुपये 1 साल की FD में रखूं तो मैच्योरिटी पर कितना मिलेगा?",
    "SBI में सेविंग्स अकाउंट पर अभी कितना ब्याज मिलता है?",
    "मैं महीने के 15000 कमाता हूँ, बैंक से सस्ता बीमा कौन सा मिलेगा?",
    "PPF क्या है और इसमें कितना ब्याज मिलता है?",
    "मैं 62 साल का हूँ, हर महीने इनकम के लिए कौन सा विकल्प अच्छा है?",
    "SBI में बचत खाता खोलने के लिए कौन से दस्तावेज चाहिए?",
    "अगर बैंक डूब जाए तो मेरा कितना पैसा सुरक्षित है?",
    "गोल्ड लोन कैसे लिया जाता है?",
    "होम लोन पर अभी कितना ब्याज लग रहा है?",
]

# Hindi, plausible as chat, and nothing a finance corpus should anchor on.
# If these clear the floor, the floor is too low and every answer gets a
# random passage attached.
HI_OFF_TOPIC = [
    "आज दिल्ली में मौसम कैसा रहेगा?",
    "मेरे फोन की बैटरी जल्दी खत्म हो जाती है, क्या करूं?",
    "दाल मखनी बनाने की विधि बताइए",
    "क्रिकेट वर्ल्ड कप 2011 कौन जीता था?",
    "मुझे हिंदी से अंग्रेजी अनुवाद करना है",
    "मेरी तबीयत ठीक नहीं है, कौन सी दवा लूं?",
    "ट्रेन का टिकट कैसे बुक करें?",
    "बच्चों के लिए कौन सा स्कूल अच्छा है?",
]

# Tamil, chosen against what the Tamil corpus actually covers -- its tagged
# topics are insurance, pension, personal_loan, deposit_safety, gold_loan,
# budgeting and senior_citizen_income. Asking it about something it has no
# passages on measures the corpus's gaps, not retrieval.
TA_ON_TOPIC = [
    "எனக்கு 62 வயசு, மாசம் வருமானத்துக்கு எந்த ஸ்கீம் நல்லது?",
    "வங்கி நொடிச்சுப் போனா என் பணம் எவ்வளவு பாதுகாப்பா இருக்கும்?",
    "தங்க நகை அடமானம் வெச்சு லோன் எப்படி வாங்குறது?",
    "எனக்கு எந்த மாதிரி இன்சூரன்ஸ் தேவை?",
    "பர்சனல் லோன் வாங்குறது நல்லதா கெட்டதா?",
    "மாச சம்பளத்தை எப்படி பிரிச்சு செலவு பண்றது?",
    "பென்ஷன் ஸ்கீம்ல எது நல்லது?",
    "FD-ல பணம் போட்டா எவ்வளவு வட்டி கிடைக்கும்?",
]

TA_OFF_TOPIC = [
    "இன்னைக்கு சென்னையில வானிலை எப்படி இருக்கும்?",
    "என் ஃபோன் பேட்டரி சீக்கிரம் தீர்ந்துடுது, என்ன பண்றது?",
    "சாம்பார் எப்படி செய்யறது?",
    "2011 உலகக் கோப்பையை யாரு ஜெயிச்சாங்க?",
    "ரயில் டிக்கெட் எப்படி புக் பண்றது?",
    "குழந்தைகளுக்கு எந்தப் பள்ளி நல்லது?",
]

CASES: dict[str, tuple[list[str], list[str]]] = {
    "hi": (HI_ON_TOPIC, HI_OFF_TOPIC),
    "ta": (TA_ON_TOPIC, TA_OFF_TOPIC),
}


def best_scores(query: str, language: str) -> list[float]:
    """Scored against one language's passages only.

    Retrieval filters by language, so evaluating against the whole index
    would measure something the runtime never does -- and would have hidden
    the fact that the index held no Tamil at all while still reporting
    healthy Hindi numbers.
    """
    index = style_examples._load_index()
    if index is None:
        raise SystemExit("No style index built. Run scripts/build_style_index.py first.")
    vector = style_examples._embed(query)
    if vector is None:
        raise SystemExit("Could not embed — is OLLAMA_HOST set and reachable?")
    passages = [p for p in index["passages"] if p.get("language") == language]
    if not passages:
        return []
    scores = [sum(a * b for a, b in zip(vector, p["embedding"])) for p in passages]
    scores.sort(reverse=True)
    return scores[:3]


def evaluate(language: str, on_topic: list[str], off_topic: list[str]) -> None:
    threshold = style_examples.MIN_SCORE
    print("=" * 62)
    print(f"  {language.upper()}")
    print("=" * 62)

    if not best_scores(on_topic[0], language):
        print(f"  NO PASSAGES for '{language}' in the index. Retrieval is a no-op\n"
              f"  for every query in this language — it will fall back to the\n"
              f"  register guide alone. Build it with --lang {language}_transcript.\n")
        return

    print("ON-TOPIC (should match — these are what the corpus covers)")
    on_best = []
    for query in on_topic:
        top = best_scores(query, language)[0]
        on_best.append(top)
        print(f"  {top:.3f} {'✓' if top >= threshold else ' '} {query[:56]}")

    print("\nOFF-TOPIC (should NOT match — nothing to do with money)")
    off_best = []
    for query in off_topic:
        top = best_scores(query, language)[0]
        off_best.append(top)
        print(f"  {top:.3f} {'✗ LEAKS' if top >= threshold else '       '} {query[:56]}")

    floor, ceiling = min(on_best), max(off_best)
    served = sum(1 for s in on_best if s >= threshold)
    print("\n  " + "-" * 58)
    print(f"  on-topic floor    {floor:.3f}   (weakest question we want served)")
    print(f"  off-topic ceiling {ceiling:.3f}   (best score junk achieves)")
    print(f"  usable gap        {floor - ceiling:+.3f}")
    print(f"  at MIN_SCORE {threshold}:  {served}/{len(on_best)} real questions served, "
          f"{sum(1 for s in off_best if s >= threshold)} junk admitted")

    if floor <= ceiling:
        print("\n  NO SEPARATION. No threshold divides these two sets: any floor that")
        print("  serves the weakest real question also lets junk through. Prefer a")
        print("  high floor and fewer examples — a missing exemplar costs nothing,")
        print("  a mismatched one changes the voice of a correct answer.")
    else:
        suggested = round((floor + ceiling) / 2, 2)
        print(f"\n  Suggested MIN_SCORE ≈ {suggested} (midpoint of the gap)")
        if not ceiling < threshold < floor:
            print(f"  Current {threshold} is OUTSIDE the gap — change it.")
        else:
            print(f"  Current {threshold} sits inside the gap. Fine as is.")
    print()


def main() -> int:
    for language, (on_topic, off_topic) in CASES.items():
        evaluate(language, on_topic, off_topic)
    # One threshold serves every language, so the binding constraint is the
    # highest off-topic ceiling across all of them, not each language's own.
    print("MIN_SCORE is global. A language whose junk scores higher than another's"
          "\nreal questions is the case this design cannot express — watch for it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
