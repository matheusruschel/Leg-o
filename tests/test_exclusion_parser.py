from lego.cli import _parse_exclusion_input


def test_empty_input_returns_empty_set():
    assert _parse_exclusion_input("", count=5) == set()
    assert _parse_exclusion_input("   ", count=5) == set()


def test_parses_comma_separated_numbers():
    assert _parse_exclusion_input("1,3,5", count=5) == {1, 3, 5}


def test_handles_whitespace_and_trailing_commas():
    assert _parse_exclusion_input(" 1 , 3 , ", count=5) == {1, 3}


def test_non_integer_returns_none():
    assert _parse_exclusion_input("1,foo,3", count=5) is None


def test_out_of_range_returns_none():
    assert _parse_exclusion_input("1,99", count=5) is None
    assert _parse_exclusion_input("0", count=5) is None  # 1-indexed


def test_deduplicates():
    assert _parse_exclusion_input("2,2,2", count=5) == {2}
