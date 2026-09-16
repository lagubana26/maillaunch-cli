import pytest

from src.campaign.csv_parser import CsvParseError, detect_email_column, parse_csv


def write_csv(tmp_path, content: str, encoding="utf-8"):
    path = tmp_path / "recipients.csv"
    path.write_bytes(content.encode(encoding))
    return str(path)


def test_standard_csv(tmp_path):
    path = write_csv(tmp_path, "name,email\nJohn Smith,john@acme.com\nJane Doe,jane@corp.com\n")
    recipients = parse_csv(path)
    assert len(recipients) == 2
    assert recipients[0].email == "john@acme.com"
    assert recipients[0].data["name"] == "John Smith"
    assert recipients[1].email == "jane@corp.com"


def test_bom_prefixed_csv(tmp_path):
    content = "\ufeffname,email\nJohn Smith,john@acme.com\n"
    path = write_csv(tmp_path, content, encoding="utf-8")
    recipients = parse_csv(path)
    assert len(recipients) == 1
    # BOM should not leak into the first header/column name
    assert "email" in recipients[0].data
    assert recipients[0].email == "john@acme.com"


def test_quoted_commas(tmp_path):
    path = write_csv(
        tmp_path,
        'name,email,company\n"Smith, John",john@acme.com,"Acme, Inc."\n',
    )
    recipients = parse_csv(path)
    assert len(recipients) == 1
    assert recipients[0].data["name"] == "Smith, John"
    assert recipients[0].data["company"] == "Acme, Inc."


def test_empty_rows_skipped(tmp_path):
    path = write_csv(
        tmp_path,
        "name,email\nJohn Smith,john@acme.com\n,\n   ,   \nJane Doe,jane@corp.com\n",
    )
    recipients = parse_csv(path)
    assert len(recipients) == 2
    assert [r.email for r in recipients] == ["john@acme.com", "jane@corp.com"]


def test_row_missing_email_is_skipped_not_fatal(tmp_path):
    path = write_csv(
        tmp_path,
        "name,email\nJohn Smith,john@acme.com\nNo Email Person,\n",
    )
    recipients = parse_csv(path)
    assert len(recipients) == 1
    assert recipients[0].email == "john@acme.com"


def test_no_rows_raises(tmp_path):
    path = write_csv(tmp_path, "name,email\n")
    with pytest.raises(CsvParseError):
        parse_csv(path)


def test_no_email_column_raises(tmp_path):
    path = write_csv(tmp_path, "name,phone\nJohn,555-1234\n")
    with pytest.raises(CsvParseError):
        parse_csv(path)


def test_explicit_email_column_override(tmp_path):
    path = write_csv(tmp_path, "name,contact\nJohn,john@acme.com\n")
    recipients = parse_csv(path, email_column="contact")
    assert recipients[0].email == "john@acme.com"


class TestDetectEmailColumn:
    def test_exact_email_preferred(self):
        assert detect_email_column(["Name", "Email", "E-mail Address"]) == "Email"

    def test_case_insensitive_mail_substring(self):
        assert detect_email_column(["Name", "WorkMail"]) == "WorkMail"

    def test_none_when_no_match(self):
        assert detect_email_column(["Name", "Phone"]) is None

    def test_empty_fieldnames(self):
        assert detect_email_column([]) is None
