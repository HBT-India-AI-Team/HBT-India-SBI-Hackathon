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
ON_TOPIC = [
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
OFF_TOPIC = [
    "आज दिल्ली में मौसम कैसा रहेगा?",
    "मेरे फोन की बैटरी जल्दी खत्म हो जाती है, क्या करूं?",
    "दाल मखनी बनाने की विधि बताइए",
    "क्रिकेट वर्ल्ड कप 2011 कौन जीता था?",
    "मुझे हिंदी से अंग्रेजी अनुवाद करना है",
    "मेरी तबीयत ठीक नहीं है, कौन सी दवा लूं?",
    "ट्रेन का टिकट कैसे बुक करें?",
    "बच्चों के लिए कौन सा स्कूल अच्छा है?",
]


def best_scores(query: str) -> list[float]:
    index = style_examples._load_index()
    if index is None:
        raise SystemExit("No style index built. Run scripts/build_style_index.py first.")
    vector = style_examples._embed(query)
    if vector is None:
        raise SystemExit("Could not embed — is OLLAMA_HOST set and reachable?")
    passages = [p for p in index["passages"] if p.get("language") == "hi"]
    scores = [sum(a * b for a, b in zip(vector, p["embedding"])) for p in passages]
    scores.sort(reverse=True)
    return scores[:3]


def main() -> int:
    print("ON-TOPIC (should match — these are what the corpus covers)")
    on_best = []
    for query in ON_TOPIC:
        top = best_scores(query)
        on_best.append(top[0])
        print(f"  {top[0]:.3f}  {query[:58]}")

    print("\nOFF-TOPIC (should NOT match — nothing to do with money)")
    off_best = []
    for query in OFF_TOPIC:
        top = best_scores(query)
        off_best.append(top[0])
        print(f"  {top[0]:.3f}  {query[:58]}")

    floor = min(on_best)
    ceiling = max(off_best)
    print("\n" + "=" * 62)
    print(f"  on-topic floor    {floor:.3f}   (weakest question we want served)")
    print(f"  off-topic ceiling {ceiling:.3f}   (best score junk achieves)")
    print(f"  usable gap        {floor - ceiling:+.3f}")
    print(f"  MIN_SCORE now     {style_examples.MIN_SCORE}")

    if floor <= ceiling:
        print("\n  NO SEPARATION. No threshold divides these two sets: any floor that")
        print("  serves the weakest real question also lets junk through. Prefer a")
        print("  high floor and fewer examples — a missing exemplar costs nothing,")
        print("  a mismatched one changes the voice of a correct answer.")
    else:
        suggested = round((floor + ceiling) / 2, 2)
        print(f"\n  Suggested MIN_SCORE ≈ {suggested} (midpoint of the gap)")
        if not ceiling < style_examples.MIN_SCORE < floor:
            print(f"  Current {style_examples.MIN_SCORE} is OUTSIDE the gap — change it.")
        else:
            print(f"  Current {style_examples.MIN_SCORE} sits inside the gap. Fine as is.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
