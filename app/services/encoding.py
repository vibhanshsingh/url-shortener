"""
Base62 encoding for turning a database ID into a short, URL-safe code
and back again.

Design decisions:

1. Alphabet order: digits, then uppercase, then lowercase. This is an
   arbitrary but conventional choice — any fixed 62-character ordering
   works correctly, since encode/decode just need to agree with each
   other. What matters is that the alphabet contains only characters
   safe to use in a URL path with zero escaping.

2. Deterministic, not random: encode(125) always returns the same
   string, and decode() of that string always returns 125. This is
   what makes collisions structurally impossible — see the module's
   companion discussion in Milestone 4 for why that matters more than
   it might first appear.

3. Optional left-padding with the zero-character: without it, id=1
   encodes to "1", which leaks how few URLs exist and makes sequential
   scraping trivial. Padding doesn't change the decoded value (same
   principle as leading zeros in decimal: "007" == 7), so it's free
   protection against that leak.
"""

BASE62_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
BASE = len(BASE62_ALPHABET)  # 62

# Reverse lookup built once at import time, not recomputed per call —
# turns decode() from an O(62) linear scan per character into an O(1)
# dict lookup per character.
_CHAR_TO_VALUE = {char: index for index, char in enumerate(BASE62_ALPHABET)}


def encode(number: int, min_length: int = 4) -> str:
    """
    Convert a non-negative integer (a urls.id value) into a Base62
    string, left-padded with the zero-character to at least
    `min_length` characters.

    Why min_length defaults to 4: with 4 characters we already cover
    62^4 ≈ 14.7 million codes before padding stops mattering (once the
    natural encoding exceeds 4 characters, padding is a no-op). That's
    enough headroom that early, low IDs don't visibly look sequential,
    without over-padding every code unnecessarily once the system has
    real scale.

    Raises:
        ValueError: if number is negative — Base62 as implemented here
            has no representation for negative numbers, and a negative
            urls.id would indicate a bug upstream, not a valid input.
    """
    if number < 0:
        raise ValueError(f"Cannot encode negative number: {number}")

    if number == 0:
        digits = BASE62_ALPHABET[0]
    else:
        chars = []
        remaining = number
        while remaining > 0:
            remaining, remainder = divmod(remaining, BASE)
            chars.append(BASE62_ALPHABET[remainder])
        # We appended least-significant digit first, so reverse to get
        # the correct most-significant-first order (same as doing long
        # division by hand for decimal — you compute digits right to
        # left, then read the answer left to right).
        digits = "".join(reversed(chars))

    return digits.rjust(min_length, BASE62_ALPHABET[0])


def decode(code: str) -> int:
    """
    Convert a Base62 string back into its original integer.

    Raises:
        ValueError: if the code is empty or contains any character
            outside the Base62 alphabet. We validate explicitly rather
            than letting a KeyError leak out of the dict lookup, so
            callers (e.g. the redirect endpoint, on a malformed short
            code in the URL) get a clear, catchable error type.
    """
    if not code:
        raise ValueError("Cannot decode an empty string")

    result = 0
    for char in code:
        if char not in _CHAR_TO_VALUE:
            raise ValueError(f"Invalid Base62 character: {char!r} in code {code!r}")
        result = result * BASE + _CHAR_TO_VALUE[char]

    return result
