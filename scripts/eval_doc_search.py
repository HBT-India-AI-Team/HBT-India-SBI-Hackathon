"""Regression check for document retrieval: does the fact still come back?

Run after ANY change to the corpus, the chunker, the embedding model or
MIN_SCORE:

    python scripts/eval_doc_search.py            # fast, retrieval only
    python scripts/eval_doc_search.py --full     # also runs the LLM end to end

Why this exists. When retrieval degrades it does not fail -- it returns
eight plausible, on-topic, correctly-cited passages that happen not to
contain the answer, and the agent writes a fluent reply with the number
missing. That already happened once here: the paragraph stating the
₹5,00,000 deposit insurance limit fell to ninth place for one phrasing of
the question, and the reply was in the right language, citing the right
regulator, silently missing the figure. Nothing errored. Nothing could
have.

So the assertion is not "did it answer" but "is the specific fact present".

The default mode checks the retrieved chunks rather than the model's reply:
it is ~100x faster, needs no chat turn, and isolates the thing that actually
regresses. --full additionally confirms the model uses what it was handed.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / "backend" / ".env")

import capabilities_impl  # noqa: E402,F401 — registers the capabilities
from capabilities_impl import doc_search  # noqa: E402

# (question as a user would ask it, substring that MUST appear in what comes
#  back, note, rephrase-or-None). Questions stay in the user's voice, not in
#  search-ese -- the point is to test what real phrasing retrieves.
#
# The fourth field is a second phrasing to try when the first misses. It
# exists because "the agent finds this" and "the first phrasing finds this"
# are different claims, and only the first one matters: FinGuru's
# instructions tell it to rephrase and search again when the passages don't
# contain the answer. A case that misses cold but lands on the retry is
# working as designed and should not read as a failure -- but it should
# still be visible, because a growing pile of them means retrieval is
# degrading even while the agent still answers.
CASES: list[tuple[str, str, str, str | None]] = [
    ("How much of my deposit is insured if my bank fails?",
     "5,00,000", "DICGC cover limit", None),
    ("Are deposits in different banks insured separately?",
     "separately", "per-bank aggregation", None),
    ("Am I liable if someone makes an unauthorised transaction on my card?",
     "demur", "card issuer must reimburse 'without demur'", None),
    ("Can a bank charge me a penalty for prepaying my home loan?",
     "pre-payment", "home loan prepayment charges", None),
    ("How do I complain about my bank if it does not resolve my issue?",
     "Ombudsman", "escalation route", None),
    ("What documents count as valid KYC proof?",
     "Officially Valid Document", "KYC OVD definition",
     "what documents are required for opening a bank account by an individual"),
    ("How do I find a bank account I forgot about?",
     "UDGAM", "unclaimed deposit search", None),
    ("What happens to money in an account nobody has touched for ten years?",
     "Depositor Education", "DEA Fund",
     "which amounts are credited to the DEA Fund"),
    # Expected strings are copied from the source text verbatim, never from
    # memory: RBI writes "₹ 2,00,000/-" and "tenor ... is 8 years", so
    # "2 lakh" and "eight years" both failed here while retrieval was fine.
    # A wrong expectation is indistinguishable from a real miss in the
    # output, so check the document before trusting a red line.
    ("What is the minimum amount for an RTGS transfer?",
     "2,00,000", "RTGS floor", None),
    ("How long do Sovereign Gold Bonds run for?",
     "8 years", "SGB tenure", None),
    ("Why can't a shopping website store my card number any more?",
     "token", "tokenisation", None),
    # -- DPDP: the law governing the agent's own use of customer data ----
    ("The bank had a data breach. What must they do and by when?",
     "72 hours", "DPDP breach notification window", None),
    ("What rights do I have over the data a company holds about me?",
     "erasure", "DPDP data principal rights", None),
    ("Does customer data have to stay inside India?",
     "within India", "DPDP data residency", None),
    ("How long must a breach register be kept?",
     "5 years", "DPDP breach register retention",
     "breach register retention period"),
    # -- SBI's own products: what an account costs and who can open one ----
    # RBI says which documents count as KYC; only the bank says what the
    # account needs, costs, or restricts. For a first-time account holder
    # that is the entire question.
    ("What is the smallest amount I can put in an SBI fixed deposit?",
     "1,000", "FD minimum deposit", None),
    ("What counts as a bulk deposit?",
     "3 crore", "bulk deposit threshold", None),
    ("How much does an SBI cheque book cost?",
     "10 Leaf Cheque Book", "cheque book charges", None),
    ("Can I open a bank account for my child?",
     "Pehla Kadam", "minor account products", None),
    ("What is a savings plus account?",
     "MOD", "MODS sweep account", None),
    # The two zero-balance products are dangerously alike and are NOT
    # interchangeable: the Small Account caps the balance at ₹50,000 and
    # stops working after 24 months without KYC, while BSBD has no ceiling
    # and no expiry. A query about one that retrieves the other produces a
    # confidently wrong answer, so each is pinned by a fact only it has.
    ("If I already have a basic savings account, can I keep my normal savings account too?",
     "cannot have any other Savings Bank Account", "BSBD exclusivity rule", None),
    ("I don't have full KYC documents yet. What account can I open, and what are the limits?",
     "50,000", "Small Account balance ceiling", None),
    ("What happens to a small account if I never submit my KYC documents?",
     "24 months", "Small Account expiry", None),
    # -- SBI products and government schemes at the bank ----
    # Expected strings are copied verbatim out of the retrieved chunk, never
    # written from memory. Cases added from memory have failed here twice
    # while retrieval was working perfectly, and a wrong expectation is
    # indistinguishable from a real miss in the output.
    ("How long is the lock-in on a tax saving fixed deposit?",
     "Lock-in period of 5 years", "tax-saving FD lock-in", None),
    ("How much can I put into a Senior Citizens Savings Scheme account?",
     "30 Lakhs", "SCSS deposit ceiling", None),
    ("What is the most I can put into PPF in a year?",
     "1,50,000", "PPF annual limit", None),
    ("How much can I borrow under Stand-Up India?",
     "1 Crore", "Stand-Up India loan range", None),
]

# Must return nothing -- the floor is doing its job.
OFF_TOPIC = [
    "Which stock should I buy tomorrow?",
    "What is the weather in Chennai?",
    "How do I cook biryani?",
    "Who won the cricket match yesterday?",
    "Give me a recipe for dosa",
]


def _find(question: str, expected: str) -> tuple[int | None, int]:
    """(1-based rank of the returned chunk containing `expected`, how many
    chunks came back). Rank matters as much as presence — a fact drifting
    from 1st to 8th is the early warning that it is about to fall off."""
    chunks = doc_search.search(question).get("results", [])
    rank = next(
        (i + 1 for i, c in enumerate(chunks) if expected.lower() in c["text"].lower()),
        None,
    )
    return rank, len(chunks)


def check_retrieval() -> int:
    failures = 0
    retries = 0
    print(f"MIN_SCORE={doc_search.MIN_SCORE}  DEFAULT_TOP_K={doc_search.DEFAULT_TOP_K}\n")
    print("-- facts that must be retrievable " + "-" * 44)
    for question, expected, note, rephrase in CASES:
        rank, count = _find(question, expected)
        if rank:
            print(f"  [ok  rank {rank}/{count}] {note:<40} {question[:38]}")
            continue
        if rephrase:
            rank, count = _find(rephrase, expected)
            if rank:
                retries += 1
                print(f"  [RETRY  {rank}/{count}] {note:<40} needed: {rephrase[:38]}")
                continue
        failures += 1
        print(f"  [{'MISS':>11}] {note:<40} {question[:38]}")
    if retries:
        print(f"\n  {retries} case(s) missed on natural phrasing and recovered on a rephrase.")
        print("  That is the documented behaviour (the agent is told to retry), but a")
        print("  rising count here means retrieval is degrading — investigate before")
        print("  it stops recovering.")

    print("\n-- off-topic, must return nothing " + "-" * 44)
    for question in OFF_TOPIC:
        result = doc_search.search(question)
        n = len(result.get("results", []))
        if n:
            failures += 1
            top = result["results"][0]["relevance"]
            print(f"  [   LEAKED {n:>2}] top={top}  {question}")
        else:
            print(f"  [{'ok':>16}] {question}")
    return failures


def check_full() -> int:
    from agent_platform.runtime.chat import handle_chat_turn

    failures = 0
    print("\n-- end to end (slow: one chat turn each) " + "-" * 36)
    for question, expected, note, _rephrase in CASES:
        try:
            reply = handle_chat_turn("finguru", None, question).reply
        except Exception as exc:  # noqa: BLE001 — a failed turn is a failed case
            failures += 1
            print(f"  [          ERROR] {note}: {type(exc).__name__}")
            continue
        if expected.lower() in reply.lower():
            print(f"  [{'ok':>16}] {note}")
        else:
            failures += 1
            print(f"  [        MISSING] {note} — expected {expected!r} in reply")
    return failures


def main() -> int:
    failures = check_retrieval()
    if "--full" in sys.argv:
        failures += check_full()
    print()
    if failures:
        print(f"FAILED — {failures} case(s). Recalibrate MIN_SCORE or widen top_k before shipping.")
    else:
        print("All cases passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
