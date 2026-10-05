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
"""

from __future__ import annotations

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
