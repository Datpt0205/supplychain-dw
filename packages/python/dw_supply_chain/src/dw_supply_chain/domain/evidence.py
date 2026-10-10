"""Evidence must be found, not asserted.

One rule for every place a model cites the text it was given: the cited span
has to actually be in that text. A supplier update's `source_ref` and every
mention a case query grounds a filter on are checked by this one function —
two copies of "what counts as verbatim" would drift the first time one of
them got more lenient.

Two differences are normalized away, because neither changes a single
character a person typed: whitespace, and Unicode composition. Vietnamese is
typed both ways — some input methods (Windows' own Vietnamese layout,
Unikey's "Unicode tổ hợp") produce "ờ" as "o" + two combining marks — and a
model echoing the quote in precomposed form has quoted it faithfully.

Two more questions about a person's words live here because more than one
reader asks them: whether a quote that becomes an identifier is whole words of
the text (`names_whole_words` — a case query's PO reference, a chat proposal's
code), and the case- and accent-insensitive form a match is made in (`fold` —
a supplier name, and the whole-message "Đồng ý" a proposal is confirmed with).
"""

from __future__ import annotations

import re
import unicodedata


def normalize(text: str) -> str:
    """Canonically composed (NFC), whitespace collapsed — the form every
    verbatim comparison is made in."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def is_verbatim(quote: str, text: str) -> bool:
    """Whether `quote` can be found in `text` — after `normalize`, never on
    the model's own say-so. An empty or blank quote cites nothing."""
    normalized = normalize(quote)
    return bool(normalized) and normalized in normalize(text)


def names_whole_words(mention: str, question: str) -> bool:
    """A mention that becomes an identifier has to be whole words of the
    question, not part of one. A letter or digit next to it extends it
    ("PO-12" inside "PO-1234"), and so does a code separator ("-", "/", ".")
    followed by one ("PO-12" inside "PO-12-A", "PO-12/1", "PO-12.5"):
    resolving either would open a PO the question never named. A separator
    that ends the sentence ("PO-12.") does not extend it."""
    if not is_verbatim(mention, question):
        return False
    pattern = r"(?<!\w)(?<!\w[-/.])" + re.escape(normalize(mention)) + r"(?!\w)(?![-/.]\w)"
    return re.search(pattern, normalize(question)) is not None


def fold(text: str) -> str:
    """Case-, accent- and spacing-insensitive form, for MATCHING only —
    "dong nai" finds "Đồng Nai". Canonical decomposition (NFD), not the
    compatibility kind: NFKD would turn "Sunhouse™" into "sunhousetm" and
    move the word boundary a whole-word match depends on. What gets used
    afterwards is always the stored name, never this form."""
    decomposed = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    unaccented = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(unaccented.casefold().split())
