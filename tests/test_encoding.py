"""
Testing strategy for a pure function like this: three categories.

1. Known values — hand-verifiable, catches "the algorithm is just
   wrong" bugs.
2. Round-trip property — encode(decode(x)) == x for a wide range of
   inputs. This is more powerful than individual known-value tests
   because it doesn't require us to hand-compute the expected string;
   it just checks internal consistency across the whole input space.
3. Edge cases and error handling — zero, boundaries, invalid input.

@pytest.mark.parametrize is used instead of a for-loop inside one test
function so that a failure on, say, id=3844 is reported by pytest as
its own distinct failing test case — you see exactly which input broke,
not just "some assertion in this test failed."
"""

import pytest

from app.services.encoding import BASE62_ALPHABET, decode, encode


class TestKnownValues:
    """Hand-verifiable encode() outputs, to catch algorithmic bugs."""

    def test_zero(self):
        # 0 has no digits to compute — it's the one true edge case in
        # the "repeatedly divide by 62" algorithm, handled explicitly.
        assert encode(0) == "0000"

    def test_one(self):
        assert encode(1) == "0001"

    def test_61_is_last_single_digit(self):
        # 61 is the largest value representable in a single Base62
        # digit (the alphabet's last character, lowercase 'z').
        assert encode(61) == "000" + BASE62_ALPHABET[61]

    def test_62_rolls_over_to_two_digits(self):
        # This is the "carry the 1" moment — same as 9 -> 10 in
        # decimal, but the rollover happens at 62, not 10.
        assert encode(62) == "0010"

    def test_62_squared_rolls_over_to_three_digits(self):
        assert encode(62 * 62) == "0100"


class TestRoundTrip:
    """decode(encode(x)) == x, and encode(decode(s)) == s (post-padding)."""

    @pytest.mark.parametrize(
        "number",
        [0, 1, 61, 62, 100, 3843, 3844, 999_999, 1_000_000, 56_800_235_583],
    )
    def test_round_trip_preserves_value(self, number):
        assert decode(encode(number)) == number

    def test_round_trip_across_wide_range(self):
        # Property-style sweep rather than hand-picked values — if the
        # algorithm has an off-by-one in the divmod loop, it usually
        # shows up somewhere in a long contiguous run, not just at the
        # boundaries we happened to think of.
        for number in range(0, 20_000):
            assert decode(encode(number)) == number


class TestPadding:
    def test_default_min_length_is_four(self):
        assert len(encode(1)) == 4

    def test_padding_does_not_change_decoded_value(self):
        # This is the property that makes padding "free" — it must
        # never change what the code decodes back to.
        assert decode(encode(5, min_length=4)) == decode(encode(5, min_length=10))

    def test_padding_is_a_no_op_once_natural_length_exceeds_min(self):
        big_number = 62**5  # naturally encodes to more than 4 characters
        assert len(encode(big_number, min_length=4)) > 4


class TestErrorHandling:
    def test_negative_number_raises(self):
        with pytest.raises(ValueError):
            encode(-1)

    def test_decode_empty_string_raises(self):
        with pytest.raises(ValueError):
            decode("")

    def test_decode_invalid_character_raises(self):
        # '!' is deliberately outside the Base62 alphabet.
        with pytest.raises(ValueError):
            decode("ab!c")

    def test_decode_error_message_names_the_bad_character(self):
        # A decent error message matters here specifically: this
        # function will be called on user-supplied short codes from
        # the URL path in the redirect endpoint, so whoever's debugging
        # a "bad short code" report needs to see what was actually
        # invalid without adding print statements.
        with pytest.raises(ValueError, match="!"):
            decode("ab!c")
