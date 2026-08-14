"""Turning a token stream into speakable sentences.

Two problems sit between "tokens are arriving" and "a sentence can be spoken",
and both are easy to get wrong in ways that only show up as bad audio.

**The stream is JSON, not prose.** The answer call runs with
`format: <output_contract>`, so what actually arrives is
`{"language":"Tamil","content_type":"text","content":"நீங்க...`. Splitting
those tokens on punctuation would send `{"language":"Tamil"` to a speech
engine. `JsonStringField` pulls the growing value of one field out of a
partial JSON document so only the answer text is ever spoken.

**Not every full stop ends a sentence.** This agent's whole output is money:
`₹1,06,398.02`, `6.25%`, `Rs. 2 lakh`. A naive split on "." cuts
"₹1,06,398.02" into "₹1,06,398." and "02" — two utterances, the first of
which is a wrong number said out loud. `SentenceSplitter` requires a boundary
after the terminator and knows the abbreviations this domain actually uses.

Both are pure and synchronous on purpose: they are the part most likely to be
wrong, and they can be tested exhaustively without a model, a network, or a
speech engine.
"""
from __future__ import annotations

# `.` `!` `?` end sentences in English and, conventionally, in written Tamil.
# `।` (U+0964) is the Devanagari danda — Hindi, Marathi, Sanskrit — and `॥`
# (U+0965) its double form. Tamil does not traditionally use either, but they
# cost nothing to accept and a mixed-script reply may contain them.
TERMINATORS = ".!?।॥…"

# Characters allowed to trail a terminator before the boundary: a quote or a
# closing bracket. `He said "yes." Then left.` must split after the quote,
# not between the stop and it.
_CLOSERS = "\"'’”)]}»"

# Case-insensitive, checked against the word immediately before a full stop.
# Short and domain-specific rather than a general English list: every entry
# here is one this agent actually emits.
_ABBREVIATIONS = frozenset({
    "rs", "mr", "mrs", "ms", "dr", "no", "vs", "etc", "approx", "inc", "ltd",
    "govt", "est", "sr", "jr", "st", "p", "a", "i", "e", "g", "u",
})


class SentenceSplitter:
    """Feed it deltas, it hands back whole sentences.

    Emits only when a sentence is provably finished — a terminator followed by
    whitespace — because the alternative is speaking half a number. Whatever
    is left over at the end comes out of `flush()`.
    """

    def __init__(self, min_chars: int = 2) -> None:
        self._buffer = ""
        self._min_chars = min_chars

    def feed(self, delta: str) -> list[str]:
        if not delta:
            return []
        self._buffer += delta
        sentences = []
        while True:
            cut = self._find_boundary()
            if cut is None:
                break
            sentence, self._buffer = self._buffer[:cut].strip(), self._buffer[cut:].lstrip()
            if len(sentence) >= self._min_chars:
                sentences.append(sentence)
            elif sentence:
                # Too short to be worth an utterance of its own; let it ride
                # with whatever comes next rather than emitting a blip.
                self._buffer = f"{sentence} {self._buffer}"
        return sentences

    def flush(self) -> str | None:
        """The trailing partial sentence. A stream rarely ends on punctuation."""
        remainder, self._buffer = self._buffer.strip(), ""
        return remainder or None

    @property
    def pending(self) -> str:
        return self._buffer

    def _find_boundary(self) -> int | None:
        """Index just past the end of the first complete sentence, or None.

        None means "not yet" rather than "no sentence here" — the deciding
        character may simply not have arrived, and guessing early is what
        splits a rupee figure in half.
        """
        for i, char in enumerate(self._buffer):
            if char not in TERMINATORS:
                continue
            if char == "." and self._is_inside_number(i):
                continue
            end = i + 1
            while end < len(self._buffer) and self._buffer[end] in _CLOSERS:
                end += 1
            if end >= len(self._buffer):
                # Terminator is the last thing we hold. Whether it ends a
                # sentence depends on the next character, which has not
                # arrived. Wait for it; flush() covers the case where the
                # stream simply stops here.
                return None
            if not self._buffer[end].isspace():
                continue
            if char == "." and self._is_abbreviation(i):
                continue
            return end
        return None

    def _is_inside_number(self, i: int) -> bool:
        """"6.25" and "₹1,06,398.02" are one token, not two sentences."""
        before = self._buffer[i - 1] if i > 0 else ""
        after = self._buffer[i + 1] if i + 1 < len(self._buffer) else ""
        return before.isdigit() and after.isdigit()

    def _is_abbreviation(self, i: int) -> bool:
        word = ""
        j = i - 1
        while j >= 0 and (self._buffer[j].isalpha() or self._buffer[j] == "."):
            word = self._buffer[j] + word
            j -= 1
        return word.replace(".", "").lower() in _ABBREVIATIONS


class JsonStringField:
    """Extracts one string field's value from a JSON document as it arrives.

    The answer stream is a JSON object built left to right, so the field we
    want to speak appears in the middle of it, escaped. This walks the raw
    text once and returns only newly decoded characters of that one value —
    never the syntax around it, and never a half-written escape sequence.

    Escapes are the fiddly part: a chunk can end mid-`\\uXXXX`, and emitting
    the fragment would put a stray backslash into speech. Anything incomplete
    stays buffered until the rest arrives.
    """

    _SIMPLE_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b",
                       "f": "\f", "n": "\n", "r": "\r", "t": "\t"}

    def __init__(self, field: str) -> None:
        self._marker = f'"{field}"'
        self._raw = ""
        self._at = 0
        self._state = "seeking"   # seeking -> inside -> done

    @property
    def done(self) -> bool:
        return self._state == "done"

    def feed(self, chunk: str) -> str:
        """Newly decoded characters of the field's value; "" if none yet."""
        if not chunk or self._state == "done":
            return ""
        self._raw += chunk
        out: list[str] = []

        while self._at < len(self._raw):
            if self._state == "seeking":
                if not self._enter_value():
                    break
                continue

            char = self._raw[self._at]
            if char == '"':
                self._state = "done"
                self._at += 1
                break
            if char != "\\":
                out.append(char)
                self._at += 1
                continue

            # An escape. Wait for all of it rather than emitting a fragment.
            if self._at + 1 >= len(self._raw):
                break
            code = self._raw[self._at + 1]
            if code == "u":
                if self._at + 6 > len(self._raw):
                    break
                try:
                    out.append(chr(int(self._raw[self._at + 2:self._at + 6], 16)))
                except ValueError:
                    out.append(self._raw[self._at + 1])
                self._at += 6
            else:
                out.append(self._SIMPLE_ESCAPES.get(code, code))
                self._at += 2

        return "".join(out)

    def _enter_value(self) -> bool:
        """Advance to the first character of the value, if it is all here."""
        found = self._raw.find(self._marker, self._at)
        if found == -1:
            # Keep only enough tail to match a marker split across chunks.
            self._at = max(self._at, len(self._raw) - len(self._marker))
            return False
        cursor = found + len(self._marker)
        while cursor < len(self._raw) and self._raw[cursor] in " \t\r\n":
            cursor += 1
        if cursor >= len(self._raw):
            return False
        if self._raw[cursor] != ":":
            # A value that merely contains the marker text, not the key.
            self._at = found + len(self._marker)
            return True
        cursor += 1
        while cursor < len(self._raw) and self._raw[cursor] in " \t\r\n":
            cursor += 1
        if cursor >= len(self._raw):
            return False
        if self._raw[cursor] != '"':
            self._at = cursor          # not a string field; nothing to speak
            self._state = "done"
            return False
        self._at = cursor + 1
        self._state = "inside"
        return True
