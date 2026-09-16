from src.campaign.template import render


def test_basic_substitution():
    result = render("Hi {{name}}, welcome to {{company}}.", {"name": "John", "company": "Acme"})
    assert result.text == "Hi John, welcome to Acme."
    assert result.missing_keys == []


def test_case_insensitive_key_matching():
    result = render("Hi {{Name}}", {"name": "John"})
    assert result.text == "Hi John"


def test_whitespace_inside_braces_tolerated():
    result = render("Hi {{ name }}", {"name": "John"})
    assert result.text == "Hi John"


def test_missing_key_left_untouched_and_reported():
    result = render("Hi {{name}}, re: {{missing_field}}", {"name": "John"})
    assert result.text == "Hi John, re: {{missing_field}}"
    assert result.missing_keys == ["missing_field"]


def test_repeated_placeholder():
    result = render("{{name}} {{name}}", {"name": "John"})
    assert result.text == "John John"


def test_nested_braces_resolve_innermost():
    # The innermost {{name}} is substituted; the extra outer braces
    # (not part of a valid {{key}} token) are left as literal text.
    result = render("{{{{name}}}}", {"name": "John"})
    assert result.text == "{{John}}"


def test_no_placeholders():
    result = render("Just plain text.", {"name": "John"})
    assert result.text == "Just plain text."
    assert result.missing_keys == []


def test_non_string_value_is_stringified():
    result = render("Count: {{count}}", {"count": 5})
    assert result.text == "Count: 5"
