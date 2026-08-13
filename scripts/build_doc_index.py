"""Builds the document index FinGuru's `docs.search` reads at runtime.

Run this to (re)build `capabilities_impl/fixtures/doc_index.json`:

    python scripts/build_doc_index.py

This is a build-time tool, not a runtime one. Nothing in the request path
fetches a web page or re-embeds anything -- the agent only ever reads the
built index. That separation is deliberate: a live fetch inside a chat turn
would put a third-party site's uptime, rate limits and latency directly in
the user's way, and the whole point of the grounding contract is that the
answer's evidence is inspectable and reproducible rather than whatever a
site happened to return that second.

Chunking follows the FAQ's own numbered questions rather than a fixed
character window, because these documents are already written as
self-contained Q&A. A chunk that starts mid-answer retrieves badly -- it
matches on wording but is missing the sentence that gives it meaning.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Both overridable so a candidate embedding model can be built into a second
# index and measured against the live one before anything is swapped. Changing
# the embedding model invalidates every calibrated number in doc_search.py
# (MIN_SCORE and the measured floor/ceiling are properties of the model, not
# of the corpus), so "build it somewhere else and compare" is the only safe
# way to evaluate one.
OUTPUT_PATH = Path(os.environ.get("DOC_INDEX_PATH") or
                   REPO_ROOT / "capabilities_impl" / "fixtures" / "doc_index.json")
EMBED_MODEL = os.environ.get("DOC_EMBED_MODEL", "nomic-embed-text")

# Public RBI customer-facing FAQs. Chosen because they answer the questions
# people actually walk into a branch with, and because they are the
# authority for those answers rather than a summary of it.
SOURCES = [
    {
        "source_id": "rbi_deposit_insurance",
        "source_name": "RBI FAQ — Deposit Insurance (DICGC)",
        "url": "https://rbi.org.in/scripts/FAQView.aspx?Id=64",
        "topic": "What bank deposits are insured, and up to how much",
    },
    {
        "source_id": "rbi_card_transactions",
        "source_name": "RBI FAQ — Card Transactions",
        "url": "https://rbi.org.in/scripts/FAQView.aspx?Id=103",
        "topic": "Debit/credit card rules and customer liability for unauthorised transactions",
    },
    {
        "source_id": "rbi_housing_loans",
        "source_name": "RBI FAQ — Housing Loans",
        "url": "https://rbi.org.in/scripts/FAQView.aspx?Id=77",
        "topic": "Home loan rules: rates, prepayment, foreclosure charges, documentation",
    },
    {
        "source_id": "rbi_ombudsman",
        "source_name": "RBI FAQ — Integrated Ombudsman Scheme",
        "url": "https://rbi.org.in/scripts/FAQView.aspx?Id=153",
        "topic": "How to complain about a bank and what the Ombudsman can do",
    },
    # -- accounts and deposits ----
    {
        "source_id": "rbi_kyc",
        "source_name": "RBI FAQ — Master Direction on KYC",
        "url": "https://rbi.org.in/scripts/FAQView.aspx?Id=173",
        "topic": "KYC requirements: accepted documents, periodic updation, re-KYC, video KYC",
    },
    {
        "source_id": "rbi_udgam",
        "source_name": "RBI FAQ — UDGAM Portal (unclaimed deposits)",
        "url": "https://rbi.org.in/scripts/FAQView.aspx?Id=166",
        "topic": "Finding and reclaiming unclaimed deposits across banks",
    },
    {
        "source_id": "rbi_dea_fund",
        "source_name": "RBI FAQ — Depositor Education and Awareness (DEA) Fund Scheme",
        "url": "https://rbi.org.in/scripts/FAQView.aspx?Id=165",
        "topic": "What happens to deposits inactive for ten years, and how to claim them back",
    },
    # Senior Citizens Savings Scheme (FAQView.aspx?Id=62) is deliberately NOT
    # here. RBI's page for it is a stub -- a title and "Date : Dec 12, 2019"
    # and nothing else -- so it yields no text to index. Adding it back would
    # make docs.list_sources advertise coverage the corpus does not have,
    # which is worse than the gap. If SCSS is needed, source it elsewhere.
    # -- credit ----
    {
        "source_id": "rbi_education_loan",
        "source_name": "RBI FAQ — Education Loan",
        "url": "https://rbi.org.in/scripts/FAQView.aspx?Id=150",
        "topic": "Education loans: eligibility, collateral, moratorium, interest subsidy",
    },
    # -- payments and fraud ----
    {
        "source_id": "rbi_atm",
        "source_name": "RBI FAQ — ATM / White Label ATM",
        "url": "https://rbi.org.in/scripts/FAQView.aspx?Id=75",
        "topic": "ATM usage, free transaction limits, charges, and failed-withdrawal complaints",
    },
    {
        "source_id": "rbi_neft",
        "source_name": "RBI FAQ — National Electronic Funds Transfer (NEFT)",
        "url": "https://rbi.org.in/scripts/FAQView.aspx?Id=60",
        "topic": "NEFT transfers: timings, limits, charges, and what to do if a transfer fails",
    },
    {
        "source_id": "rbi_rtgs",
        "source_name": "RBI FAQ — Real Time Gross Settlement (RTGS)",
        "url": "https://rbi.org.in/scripts/FAQView.aspx?Id=65",
        "topic": "RTGS transfers: minimum amount, timings, charges, and failed-transfer recourse",
    },
    {
        "source_id": "rbi_ppi",
        "source_name": "RBI FAQ — Prepaid Payment Instruments (PPIs)",
        "url": "https://rbi.org.in/scripts/FAQView.aspx?Id=126",
        "topic": "Wallets and prepaid cards: KYC limits, loading, transfers, and closure",
    },
    {
        "source_id": "rbi_cheque_clearing",
        "source_name": "RBI FAQ — Cheque Clearing",
        "url": "https://rbi.org.in/scripts/FAQView.aspx?Id=175",
        "topic": "Cheque clearing timelines, positive pay, dishonour and charges",
    },
    {
        "source_id": "rbi_tokenisation",
        "source_name": "RBI FAQ — Device-based Tokenisation of Card Transactions",
        "url": "https://rbi.org.in/scripts/FAQView.aspx?Id=129",
        "topic": "Card tokenisation: what a token is, consent, and why merchants can't store card numbers",
    },
    # -- investments ----
    {
        "source_id": "rbi_sgb",
        "source_name": "RBI FAQ — Sovereign Gold Bond Scheme",
        "url": "https://rbi.org.in/scripts/FAQView.aspx?Id=109",
        "topic": "Sovereign Gold Bonds: tenure, interest, redemption, tax treatment and limits",
    },
    # -- SBI's own products ----
    # The gap these fill: RBI says what documents count as KYC, but nothing
    # about what an account actually costs, what balance it needs, or which
    # one a person should open. For someone opening their first bank account
    # that is the whole question, and it is the one thing a general model
    # cannot answer for a specific bank.
    #
    # Product detail pages only. SBI's savings-account landing page lists
    # nine account types and describes none of them -- it extracts as link
    # labels. The "Revised Service Charges" page is the same trap and was
    # deliberately left out: it is 18 PDF link titles ("Revised IMPS
    # Transaction Charges On Or After 15.02.2026") with no amount behind any
    # of them, so it would rank first for fee questions and answer none.
    # If those charges are ever needed, the PDFs have to be ingested, not
    # the page listing them.
    {
        "source_id": "sbi_savings_account",
        "source_name": "SBI — Savings Bank Account",
        "url": "https://sbi.bank.in/web/personal-banking/accounts/saving-account/savings-bank-account",
        "extractor": "sbi",
        "topic": "SBI's standard savings account: minimum balance, cheque book charges, "
                    "passbook, nomination, who is eligible and modes of operation",
    },
    {
        "source_id": "sbi_bsbd",
        "source_name": "SBI — Basic Savings Bank Deposit Account",
        "url": "https://sbi.bank.in/web/personal-banking/accounts/saving-account/basic-savings-bank-deposit-account",
        "extractor": "sbi",
        "topic": "Zero-balance BSBD account: eligibility, free withdrawal limits, RuPay "
                    "card, and the rule that it cannot be held alongside another savings account",
    },
    {
        "source_id": "sbi_bsbd_small",
        "source_name": "SBI — Basic Savings Bank Deposit Small Account",
        "url": "https://sbi.bank.in/web/personal-banking/accounts/saving-account/basic-savings-bank-deposit-small-account",
        "extractor": "sbi",
        "topic": "Small account for someone without full KYC documents: balance and "
                    "transaction ceilings, validity period, and what is needed to upgrade it",
    },
    {
        "source_id": "sbi_savings_minor",
        "source_name": "SBI — Savings Account for Minors (Pehla Kadam / Pehli Udaan)",
        "url": "https://sbi.bank.in/web/personal-banking/accounts/saving-account/savings-account-for-minors",
        "extractor": "sbi",
        "topic": "Accounts for children: age rules, who operates the account, transaction "
                    "limits and the facilities included",
    },
    {
        "source_id": "sbi_savings_plus",
        "source_name": "SBI — Savings Plus Account (MODS)",
        "url": "https://sbi.bank.in/web/personal-banking/accounts/saving-account/savings-plus-account",
        "extractor": "sbi",
        "topic": "Savings account linked to a multi-option deposit, where surplus balance "
                    "sweeps into a term deposit: deposit period, thresholds and loans against it",
    },
    {
        "source_id": "sbi_term_deposit",
        "source_name": "SBI — Term Deposit (fixed deposit) product terms",
        "url": "https://sbi.bank.in/web/personal-banking/investments-deposits/deposits/fixed-deposit",
        "extractor": "sbi",
        "topic": "FD terms as opposed to FD rates: minimum and maximum period and amount, "
                    "the bulk-deposit threshold, interest payout frequency, TDS and Form "
                    "15G/15H, premature withdrawal and loan against deposit",
    },
    {
        "source_id": "rbi_pension",
        "source_name": "RBI FAQ — Payment of Pension to Government Pensioners",
        "url": "https://rbi.org.in/scripts/FAQView.aspx?Id=68",
        "topic": "How government pensions are paid through banks: life certificates, arrears, "
                    "joint accounts, and what to do when a pension is not credited",
    },
    # A sweep of RBI's whole FAQ Id space found 80 more pages, and 79 were
    # deliberately left out. They are real and substantial -- FEMA compounding,
    # NBFC licensing, Government Securities, NDS-OM, external commercial
    # borrowings, liaison offices of foreign entities -- and every one is
    # written for institutions, not customers. Adding them would raise the
    # off-topic ceiling (more chunks means more chances something scores close
    # by coincidence) while answering no retail question, and the usable gap
    # is already down to 0.039. Corpus growth is not free here; see MIN_SCORE
    # in capabilities_impl/doc_search.py.

    # -- SBI's own products ----
    # The personal-loan pages matter more than their size suggests: the corpus
    # previously held ZERO personal-loan content, and a personal-loan question
    # came back citing the RBI Housing Loans FAQ. Retrieval was working; there
    # was simply nothing right to find.
    {
        "source_id": "sbi_personal_loan",
        "source_name": "SBI — Personal Loan (Xpress Credit)",
        "url": "https://sbi.bank.in/web/personal-banking/loans/personal-loans/sbi-personal-loan",
        "extractor": "sbi",
        "topic": "SBI personal loans: who is eligible, loan amounts, tenure, salary-account "
                    "requirements, processing fees and prepayment terms",
    },
    {
        "source_id": "sbi_pensioner_loan",
        "source_name": "SBI — Loans to Pensioners",
        "url": "https://sbi.bank.in/web/personal-banking/loans/loans-to-pensioners",
        "extractor": "sbi",
        "topic": "Loans for pensioners: age limits, eligible pension types, amounts, "
                    "repayment period and guarantor requirements",
    },
    {
        "source_id": "sbi_education_loan_product",
        "source_name": "SBI — Student Loan Scheme",
        "url": "https://sbi.bank.in/web/personal-banking/loans/education-loans/student-loan-scheme",
        "extractor": "sbi",
        "topic": "SBI's own education loan product: amounts, margin, collateral thresholds, "
                    "moratorium and repayment — as opposed to RBI's rules on education loans",
    },
    {
        "source_id": "sbi_savings_rules",
        "source_name": "SBI — Savings Bank Rules (Abridged)",
        "url": "https://sbi.bank.in/web/personal-banking/accounts/saving-account/savings-bank-rulesabridged",
        "extractor": "sbi",
        "topic": "The account rules a customer actually signs up to: operation, nomination, "
                    "closure, dormancy, charges and the bank's obligations",
    },
    {
        "source_id": "sbi_locker",
        "source_name": "SBI — Safe Deposit Lockers",
        "url": "https://sbi.bank.in/web/personal-banking/information-services/kyc-guidelines/safe-deposit-vaults",
        "extractor": "sbi",
        "topic": "Safe deposit lockers: eligibility, agreement, access, rent, nomination, "
                    "and the bank's liability if contents are lost",
    },
    {
        "source_id": "sbi_recurring_deposit",
        "source_name": "SBI — Recurring Deposit",
        "url": "https://sbi.bank.in/web/personal-banking/investments-deposits/deposits/recurring-deposit",
        "extractor": "sbi",
        "topic": "Recurring deposits: monthly instalment limits, tenure, penalties for missed "
                    "instalments, premature closure and loan against the deposit",
    },
    {
        "source_id": "sbi_tax_saving_fd",
        "source_name": "SBI — Tax Savings Scheme 2006 (tax-saving FD)",
        "url": "https://sbi.bank.in/web/personal-banking/investments-deposits/deposits/sbi-tax-savings-scheme-2006",
        "extractor": "sbi",
        "topic": "The 5-year tax-saving fixed deposit that qualifies for Section 80C: lock-in, "
                    "minimum and maximum amounts, and why it cannot be broken early",
    },
    {
        "source_id": "sbi_mod",
        "source_name": "SBI — Multi Option Deposit (MOD)",
        "url": "https://sbi.bank.in/web/personal-banking/investments-deposits/deposits/mod",
        "extractor": "sbi",
        "topic": "Deposits linked to a savings account that break in units when money is "
                    "needed, so only the withdrawn part loses interest",
    },
    {
        "source_id": "sbi_special_term_deposit",
        "source_name": "SBI — Special Term Deposit (reinvestment plan)",
        "url": "https://sbi.bank.in/web/personal-banking/investments-deposits/deposits/reinvestment-plan",
        "extractor": "sbi",
        "topic": "The cumulative term deposit where interest is reinvested rather than paid "
                    "out, and how that differs from a regular term deposit",
    },
    {
        "source_id": "sbi_annuity_deposit",
        "source_name": "SBI — Annuity Deposit Scheme",
        "url": "https://sbi.bank.in/web/personal-banking/investments-deposits/deposits/annuity-deposit-scheme",
        "extractor": "sbi",
        "topic": "Paying in a lump sum and drawing it back as a monthly annuity — for "
                    "retirement income questions",
    },
    {
        "source_id": "sbi_har_ghar_lakhpati",
        "source_name": "SBI — Har Ghar Lakhpati (recurring deposit variant)",
        "url": "https://sbi.bank.in/web/personal-banking/investments-deposits/deposits/har-ghar-lakhpati",
        "extractor": "sbi",
        "topic": "A goal-based recurring deposit aimed at accumulating ₹1,00,000 or multiples, "
                    "including a variant for minors",
    },
    {
        "source_id": "sbi_ppf",
        "source_name": "SBI — Public Provident Fund (PPF)",
        "url": "https://sbi.bank.in/web/personal-banking/investments-deposits/govt-schemes/ppf",
        "extractor": "sbi",
        "topic": "PPF as operated by the bank: deposit limits, 15-year term, extension, "
                    "partial withdrawal, loans against the balance and tax treatment",
    },
    {
        "source_id": "sbi_sukanya",
        "source_name": "SBI — Sukanya Samriddhi Account",
        "url": "https://sbi.bank.in/web/personal-banking/investments-deposits/govt-schemes/sukanya-samriddhi-yojana",
        "extractor": "sbi",
        "topic": "The girl-child savings scheme: who can open it and by what age, deposit "
                    "limits, maturity, and withdrawal for education or marriage",
    },
    {
        "source_id": "sbi_scss",
        "source_name": "SBI — Senior Citizens Savings Scheme",
        "url": "https://sbi.bank.in/web/personal-banking/investments-deposits/govt-schemes/senior-citizens-savings-scheme",
        "extractor": "sbi",
        "topic": "SCSS for people aged 60 and over: eligibility including early retirees, "
                    "deposit ceiling, term, premature closure and interest payment",
    },
    {
        "source_id": "sbi_capital_gains",
        "source_name": "SBI — Capgains Plus (Capital Gains Account Scheme 1988)",
        "url": "https://sbi.bank.in/web/personal-banking/investments-deposits/govt-schemes/capital-gains-account",
        "extractor": "sbi",
        "topic": "Parking capital gains to defer tax while a new property is bought or built: "
                    "account types, deadlines and withdrawal rules",
    },
    {
        "source_id": "sbi_rbi_bonds",
        "source_name": "SBI — RBI Floating Rate Savings Bonds",
        "url": "https://sbi.bank.in/web/personal-banking/investments-deposits/govt-schemes/rbi-bonds",
        "extractor": "sbi",
        "topic": "Government floating rate savings bonds sold through the bank: tenure, "
                    "interest reset, taxation and premature encashment for senior citizens",
    },
    {
        "source_id": "standup_india",
        "source_name": "Stand-Up India — scheme details",
        "url": "https://www.standupmitra.in/Home/SUISchemes",
        "extractor": "gov",
        "topic": "Bank loans between ₹10 lakh and ₹1 crore for SC/ST and women entrepreneurs "
                    "setting up a new enterprise",
    },
    # -- government schemes ----
    # Only the two portals that actually serve extractable text. Deliberately
    # NOT indexed, having been probed and found unusable:
    #   jansuraksha.gov.in  - 72 characters total; the scheme content is
    #                         rendered by JavaScript
    #   nsiindia.gov.in     - pure navigation menu, bilingual, no scheme text
    #   financialservices.gov.in - 404s to any non-browser fetch
    # Their numbers (PMJJBY/PMSBY/APY premiums, PPF/SSY/SCSS rates) live in
    # fixtures/india_reference_rates.json under `government_schemes` instead,
    # behind india.get_scheme_details. That is the better home regardless:
    # small savings rates are revised quarterly and the fixture carries
    # per-entry as_of/max_age_days, which this corpus cannot express.
    {
        "source_id": "pmjdy",
        "source_name": "Pradhan Mantri Jan-Dhan Yojana — scheme details",
        "url": "https://pmjdy.gov.in/scheme",
        "extractor": "gov",
        "topic": "The national financial-inclusion scheme: zero-balance account for the "
                    "unbanked, RuPay card accident cover, overdraft facility, and the other "
                    "schemes a Jan Dhan account makes you eligible for",
    },
    {
        "source_id": "mudra_pmmy",
        "source_name": "Pradhan Mantri MUDRA Yojana (PMMY) — scheme details",
        "url": "https://www.mudra.org.in/",
        "extractor": "gov",
        "topic": "Collateral-free loans for small and micro businesses: the Shishu, Kishor, "
                    "Tarun and Tarun Plus categories, who lends, and how to apply",
    },
    # -- data protection ----
    # Local files rather than URLs: statute isn't served as parseable HTML,
    # and a law's text must not change under the index between builds.
    {
        "source_id": "dpdp_act_2023",
        "source_name": "Digital Personal Data Protection Act, 2023",
        "path": "../DPDP2023.pdf",
        "url": "https://www.meity.gov.in/data-protection-framework",
        "topic": "The DPDP Act itself: consent, data principal rights, obligations of data "
                    "fiduciaries, breach duties and penalties",
    },
    # NOT the gazetted Rules, despite the filename. This is an internal
    # compliance framework: a paraphrase of DPDP obligations under invented
    # "Chapter/Clause" numbering (the real Rules are numbered Rule 1-23 with
    # Schedules), plus SBI's own policies, data classifications and
    # workflows. Labelled accordingly, because citing "Clause 7.1 of the
    # DPDP Rules" points at a clause that does not exist in the law -- an
    # invented citation is worse than none, and this is a document a judge
    # can check. The statute itself is indexed separately as dpdp_act_2023.
    #
    # The internal half is the genuinely valuable part: retention schedules,
    # the breach workflow, data-category access rules. No public model can
    # know any of it.
    {
        "source_id": "sbi_privacy_policy",
        "source_name": "SBI Data Privacy Compliance Framework (internal)",
        "path": "DPDP_Rules_2025.md",
        # No public URL: this is an internal document. Attaching meity.gov.in
        # would imply the government published SBI's retention schedule, and
        # a citation that points somewhere the claim isn't is worse than a
        # citation that admits it is internal.
        "url": "internal://sbi-data-privacy-compliance-framework",
        "topic": "SBI's INTERNAL data-privacy policy and its restatement of DPDP obligations: "
                    "consent architecture, retention schedules per data category, breach response "
                    "workflow and SLAs, localisation controls. Internal policy — for the law "
                    "itself cite the DPDP Act, 2023, not this.",
    },
]

MIN_CHUNK_CHARS = 120
MAX_CHUNK_CHARS = 1800

# "1. Which banks are insured?" / "Q3." -- the FAQ's own numbering.
_QUESTION_START = re.compile(r"^(?:Q\s*)?\d+\s*[\.\)]\s+\S")

# A markdown heading ("### Clause 2.1 - Consent Requirements", "## Chapter 2")
# or a statute section start. Legal text is written in numbered, citable
# units; splitting anywhere else produces a chunk that cannot be cited, which
# for a compliance corpus is the whole value gone.
_SECTION_START = re.compile(r"^(#{2,4}\s+\S|Section\s+\d+|CHAPTER\s+[IVXL]+)")


def _looks_like_garbled_devanagari(line: str) -> bool:
    """True for the ASCII mush a legacy non-Unicode Devanagari font leaves
    behind ("vlk/kkj.k", "izkf/kdkj ls izdkf'kr").

    India's Gazette PDFs are bilingual and the Hindi half is often set in a
    pre-Unicode font, so extraction yields consonant soup rather than either
    readable Hindi or nothing. It survives every obvious filter -- it is
    plain ASCII, the right length, and full of letters -- but it embeds as
    noise and can be retrieved and quoted. English prose runs about 38%
    vowels; this runs far lower, which separates them cleanly.
    """
    letters = [c for c in line.lower() if c.isalpha()]
    if len(letters) < 6:
        return False
    return sum(1 for c in letters if c in "aeiou") / len(letters) < 0.25


# Running headers/footers repeated on every Gazette page.
_PDF_NOISE = re.compile(
    r"THE GAZETTE OF INDIA|EXTRAORDINARY|PUBLISHED\s+BY\s+AUTHORITY|"
    r"REGISTERED\s+NO|xxxGID|Separate paging",
    re.I,
)


def read_pdf(path: Path) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    raw = "\n".join((page.extract_text() or "") for page in reader.pages)

    blocks: list[str] = []
    for line in raw.split("\n"):
        line = re.sub(r"\s+", " ", line).strip()
        if not line or _PDF_NOISE.search(line) or _looks_like_garbled_devanagari(line):
            continue
        blocks.append(line)
    return blocks


def read_markdown(path: Path) -> list[str]:
    """Markdown arrives already structured, so keep headings as their own
    blocks — chunk() starts a new chunk at each one."""
    blocks: list[str] = []
    for line in path.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if not line or line.startswith("---"):
            continue
        blocks.append(line)
    return blocks


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (FinGuru doc indexer)"})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read().decode("utf-8", errors="ignore")


def extract_blocks(html: str) -> list[str]:
    """The readable text of the FAQ body, as a list of paragraph-ish blocks."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()

    # RBI renders the FAQ body into one of these; fall back to the largest
    # table if the page template changes, which it periodically does.
    container = soup.select_one("div#annual.text1") or soup.select_one("table.td")
    if container is None:
        tables = soup.find_all("table")
        container = max(tables, key=lambda t: len(t.get_text(strip=True)), default=None)
    if container is None:
        return []

    blocks: list[str] = []
    for element in container.find_all(["p", "li", "td", "div"]):
        # Only leaf-ish nodes, or every paragraph gets counted once per ancestor.
        if element.find(["p", "li", "table"]):
            continue
        text = element.get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) >= 25:
            blocks.append(text)

    deduped: list[str] = []
    for block in blocks:
        if block not in deduped:
            deduped.append(block)
    return deduped


# Every SBI page carries the same AI-translation notice and a feedback form.
# Both are long enough to survive the length filter and are about the website
# rather than the product, so indexed they would match any question about
# accuracy or liability with text that answers none of it.
_SBI_CHROME = re.compile(
    r"translated from English version|Bhashini|AI generated, we request|"
    r"Thank you for sharing your feedback|10 digit Indian mobile",
    re.I,
)

# The product content ends here. Everything below is related-product links and
# footer navigation, which extract as short plausible-looking fragments
# ("Savings Account for Minors", "Download Account Opening Form") that are
# pure noise in a retrieval index.
_SBI_CONTENT_END = re.compile(r"Last Updated On\s*:", re.I)


# Government portals publish English and Hindi in the SAME element, so a block
# comes out as the English sentence immediately followed by its Devanagari
# translation. That has to be cut, for two reasons: the index and the query
# embedding model are both English-first, so a bilingual block embeds as a
# muddier vector than either half alone; and the Hindi half carries Devanagari
# numerals ("२0 लाख"), which can be quoted straight into an answer and breaks
# the Western-digits rule the skill spends three paragraphs on.
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def _english_half(text: str) -> str:
    """The text up to the first Devanagari character.

    Crude on purpose. These pages put English first and Hindi second with no
    separator to key off, so position is the only reliable signal — and a
    block that turns out to be Hindi-first simply comes back empty and gets
    dropped by the length filter, which is the safe failure.
    """
    match = _DEVANAGARI.search(text)
    return text[:match.start()].strip() if match else text


def extract_gov_blocks(html: str) -> list[str]:
    """Readable text from a bilingual Indian government scheme portal.

    Same shape as extract_sbi_blocks, plus the English/Hindi split and a
    filter for the running totals these sites lead with ("Amount Sanctioned
    190861.92 Crore"). Those change daily, mean nothing to a retail customer
    asking whether they qualify for a loan, and would age the corpus for no
    benefit.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()

    noise = re.compile(
        r"Amount (Sanctioned|Disbursed)|No\. of PMMY Loans|Financial Year\s*:|"
        r"Vigilance Awareness|Content on this website is managed|Powered by",
        re.I,
    )

    blocks: list[str] = []
    for element in soup.find_all(["h2", "h3", "h4", "p", "li", "td"]):
        if element.find(["p", "li", "table"]):
            continue
        text = re.sub(r"\s+", " ", element.get_text(" ", strip=True))
        text = _english_half(text)
        if len(text) < 40 or noise.search(text):
            continue
        blocks.append(text)

    deduped: list[str] = []
    for block in blocks:
        if block not in deduped:
            deduped.append(block)
    return deduped


def extract_sbi_blocks(html: str) -> list[str]:
    """Readable product text from an SBI personal-banking page.

    Separate from extract_blocks() because the two sites need opposite
    strategies. RBI renders a whole FAQ into one identifiable container, so
    that function finds the container and takes everything inside it. SBI
    nests its content many levels deep with no stable wrapper class, but
    marks the *end* of it with a "Last Updated On" stamp -- so here the
    boundary is found by scanning forward and stopping, rather than by
    locating a box.

    Only use this on product detail pages. SBI's category landing pages list
    nine account types and describe none of them; they extract as a pile of
    link labels that would rank for account questions and answer nothing.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()

    blocks: list[str] = []
    for element in soup.find_all(["h2", "h3", "h4", "p", "li", "td"]):
        if element.find(["p", "li", "table"]):
            continue
        text = re.sub(r"\s+", " ", element.get_text(" ", strip=True))
        if _SBI_CONTENT_END.search(text):
            break
        if len(text) < 25 or _SBI_CHROME.search(text):
            continue
        blocks.append(text)

    deduped: list[str] = []
    for block in blocks:
        if block not in deduped:
            deduped.append(block)
    return deduped


def chunk(blocks: list[str]) -> list[dict]:
    """Groups blocks into chunks that start at a numbered question."""
    chunks: list[dict] = []
    current: list[str] = []
    heading = None

    def flush() -> None:
        if not current:
            return
        text = " ".join(current).strip()
        if len(text) >= MIN_CHUNK_CHARS:
            chunks.append({"heading": heading, "text": text})

    for block in blocks:
        starts_question = bool(_QUESTION_START.match(block)) or bool(_SECTION_START.match(block))
        too_long = sum(len(b) for b in current) + len(block) > MAX_CHUNK_CHARS
        if current and (starts_question or too_long):
            flush()
            current = []
        if starts_question:
            heading = block.lstrip("#").strip()[:200]
        current.append(block)
    flush()
    return chunks


# "reply to Q 5 above", "Question 13", "Q. 14 and 15", "detailed in Q 24".
_REFERENCE = re.compile(r"Q(?:uestion)?\s*\.?\s*(\d+)(?:\s*(?:,|and)\s*(\d+))?", re.I)
# Chunk headings look like "5. What ..." or "Q 5. What ...".
_HEADING_NUMBER = re.compile(r"^\s*(?:Q(?:uestion)?\s*\.?\s*)?(\d+)\s*[\.\)]")

# A resolved reference is appended, not merged: keep it bounded so one chunk
# can't absorb a whole document and become "about" everything.
MAX_REFERENCE_CHARS = 900


def resolve_cross_references(pieces: list[dict]) -> int:
    """Inlines the text a chunk points at instead of leaving a dangling
    pointer. Returns how many chunks were amended.

    FAQs cross-reference constantly -- "documents as mentioned in the reply
    to Q 5 above" -- and chunking severs the link, because Q5's answer lives
    in a different chunk. The retrieved passage is then *plausible and
    incomplete*: it names no documents, cites the right page, and reads
    fine. Retrieval-retry doesn't rescue it either, since the model can't
    know it's missing a list it never saw.

    Only one level is resolved, and only within the same document. Chasing
    references transitively would pull half the FAQ into a single chunk and
    reintroduce the "one vector for everything" problem chunking exists to
    avoid.
    """
    by_number: dict[str, dict] = {}
    for piece in pieces:
        match = _HEADING_NUMBER.match(piece["heading"] or "")
        if match:
            by_number.setdefault(match.group(1), piece)

    amended = 0
    for piece in pieces:
        own = _HEADING_NUMBER.match(piece["heading"] or "")
        own_number = own.group(1) if own else None

        # Skip the heading itself, or "Q 5." at the start reads as a
        # reference to the very chunk it labels.
        body = piece["text"][len(piece["heading"] or ""):]

        wanted: list[str] = []
        for match in _REFERENCE.finditer(body):
            for number in match.groups():
                if number and number != own_number and number not in wanted:
                    wanted.append(number)

        additions = []
        for number in wanted:
            target = by_number.get(number)
            if target is None or target is piece:
                continue
            snippet = target["text"][:MAX_REFERENCE_CHARS]
            additions.append(f"[Referenced Q{number}] {snippet}")
        if additions:
            piece["text"] = piece["text"] + "\n\n" + "\n".join(additions)
            piece["resolved_references"] = wanted
            amended += 1
    return amended


# Embeddings already computed, keyed by (model, text). Survives between runs
# in a file next to the index, so a build interrupted at chunk 100 of 473
# resumes at 100 instead of starting over.
#
# This exists because the Ollama host is a shared ngrok tunnel that flaps: it
# went down mid-build three times in one afternoon, once for longer than any
# reasonable retry window. Retrying handles a dropped connection; only a cache
# handles the host being gone for five minutes. Chunk text is the key rather
# than chunk_id because ids shift when a source is added, while a paragraph
# that hasn't changed shouldn't be paid for twice.
_CACHE_PATH = OUTPUT_PATH.with_suffix(".embedcache.json")
_cache: dict[str, list[float]] | None = None


def _cache_key(text: str) -> str:
    return hashlib.sha256(f"{EMBED_MODEL}\x00{text}".encode()).hexdigest()


def _load_cache() -> dict[str, list[float]]:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _save_cache() -> None:
    if _cache:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(_cache), encoding="utf-8")


def embed(text: str, host: str, attempts: int = 4) -> list[float]:
    """One embedding call, with retries, because this runs hundreds of times
    in a row over an ngrok tunnel.

    Without this a single dropped connection anywhere in the sequence throws
    away the whole build -- and it happens: two builds died mid-run here, one
    on RemoteDisconnected at chunk ~200, having already spent minutes fetching
    and embedding. The tunnel is documented elsewhere in this repo as
    occasionally 503-ing back-to-back requests, so a transient failure is the
    expected case, not the exceptional one. OllamaAdapter already retries for
    exactly this reason; the build script was the one path that didn't.
    """
    cache = _load_cache()
    key = _cache_key(text)
    if key in cache:
        return cache[key]

    body = json.dumps({"model": EMBED_MODEL, "prompt": text}).encode()
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            host.rstrip("/") + "/api/embeddings", data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.load(response)
            vector = payload.get("embedding")
            if vector:
                cache[key] = vector
                return vector
            last_error = RuntimeError(f"no embedding returned for: {text[:80]!r}")
        except Exception as exc:  # noqa: BLE001 — any transport failure is worth retrying
            last_error = exc
        if attempt < attempts:
            # Backs off far enough to ride out a short outage, not just a
            # dropped connection: 5s, 15s, 45s, 135s. The tunnel has been
            # observed down for minutes, and a build that gives up after 18
            # seconds throws away everything computed so far -- which the
            # cache now preserves anyway, making patience nearly free.
            wait = 5 * (3 ** (attempt - 1))
            print(f"    embed attempt {attempt}/{attempts} failed "
                  f"({type(last_error).__name__}); retrying in {wait}s", flush=True)
            # Checkpoint before a long wait, so a kill during it loses nothing.
            _save_cache()
            time.sleep(wait)
    _save_cache()
    raise RuntimeError(f"embedding failed after {attempts} attempts: {last_error}")


def main() -> int:
    import os

    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / "backend" / ".env")
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

    records = []
    empty: list[str] = []
    for source in SOURCES:
        print(f"fetching {source['source_id']} ...", flush=True)
        local = source.get("path")
        if local:
            local_path = (REPO_ROOT / local).resolve()
            if not local_path.exists():
                raise SystemExit(f"source {source['source_id']}: file not found at {local_path}")
            blocks = (read_pdf(local_path) if local_path.suffix.lower() == ".pdf"
                        else read_markdown(local_path))
        elif source.get("extractor") == "sbi":
            blocks = extract_sbi_blocks(fetch(source["url"]))
        elif source.get("extractor") == "gov":
            blocks = extract_gov_blocks(fetch(source["url"]))
        else:
            blocks = extract_blocks(fetch(source["url"]))
        pieces = chunk(blocks)
        amended = resolve_cross_references(pieces)
        note = f", {amended} cross-ref{'s' if amended != 1 else ''} resolved" if amended else ""
        print(f"  {len(blocks)} blocks -> {len(pieces)} chunks{note}", flush=True)
        if not pieces:
            empty.append(f"{source['source_id']} ({source['url']})")
        for index, piece in enumerate(pieces):
            records.append({
                "chunk_id": f"{source['source_id']}#{index:03d}",
                "source_id": source["source_id"],
                "source_name": source["source_name"],
                "source_url": source["url"],
                "topic": source["topic"],
                "heading": piece["heading"],
                "text": piece["text"],
                "resolved_references": piece.get("resolved_references", []),
            })

    # Fail here, before the slow part, and fail loudly. A source that yields
    # nothing is nearly always RBI changing a page template or replacing a
    # page with a stub -- and the damage is silent: the index still builds,
    # docs.list_sources still advertises the topic, and the agent confidently
    # reports no coverage for questions it was supposed to answer. Aborting
    # costs a rebuild; shipping it costs a wrong answer nobody notices.
    if empty:
        print("\nABORTING — these sources produced no text:", file=sys.stderr)
        for item in empty:
            print(f"  - {item}", file=sys.stderr)
        print("\nCheck the page in a browser: RBI sometimes replaces an FAQ with a "
              "stub, or changes the template so extract_blocks() finds nothing. "
              "Fix the source or remove it from SOURCES; do not ship it empty.",
              file=sys.stderr)
        return 1

    print(f"embedding {len(records)} chunks with {EMBED_MODEL} ...", flush=True)
    for i, record in enumerate(records, 1):
        # The heading is prepended so a chunk carries its question into its
        # own vector -- answers often don't restate what they're answering.
        subject = f"{record['heading']} {record['text']}" if record["heading"] else record["text"]
        record["embedding"] = embed(subject, host)
        if i % 20 == 0:
            _save_cache()   # checkpoint, so a crash costs at most 20 chunks
            print(f"  {i}/{len(records)}", flush=True)
    _save_cache()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps({
        "built_at": datetime.now(timezone.utc).isoformat(),
        "retrieved_on": date.today().isoformat(),
        "embedding_model": EMBED_MODEL,
        "embedding_dimensions": len(records[0]["embedding"]) if records else 0,
        "sources": [{k: v for k, v in s.items()} for s in SOURCES],
        "chunks": records,
    }, ensure_ascii=False), encoding="utf-8")

    size_mb = OUTPUT_PATH.stat().st_size / 1e6
    print(f"\nwrote {OUTPUT_PATH.relative_to(REPO_ROOT)} — {len(records)} chunks, {size_mb:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
