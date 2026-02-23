from opencane.channels.text_split import split_message


def test_split_message_empty_returns_empty_list() -> None:
    assert split_message("", 10) == []


def test_split_message_keeps_short_content() -> None:
    assert split_message("hello", 10) == ["hello"]


def test_split_message_prefers_newline_boundary() -> None:
    text = "line1\nline2\nline3"
    chunks = split_message(text, 8)
    assert chunks == ["line1", "line2", "line3"]
    assert all(len(item) <= 8 for item in chunks)


def test_split_message_falls_back_to_space_boundary() -> None:
    text = "alpha beta gamma"
    chunks = split_message(text, 10)
    assert chunks == ["alpha beta", "gamma"]
    assert all(len(item) <= 10 for item in chunks)


def test_split_message_hard_splits_when_no_boundary() -> None:
    text = "abcdefghijklmnopqrstuvwxyz"
    chunks = split_message(text, 7)
    assert chunks == ["abcdefg", "hijklmn", "opqrstu", "vwxyz"]
    assert all(len(item) <= 7 for item in chunks)

