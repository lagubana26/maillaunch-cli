"""
CSV recipient list parsing.

Handles the same edge cases as the original maillaunch.html:
  - UTF-8 BOM prefix
  - Quoted fields containing commas
  - Empty / blank rows (skipped)
  - Case-insensitive auto-detection of the email column
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import List, Optional


class CsvParseError(ValueError):
    pass


@dataclass
class Recipient:
    row_index: int  # 0-based index into the original (non-empty) rows
    email: str
    data: dict  # full row, including the email column, for templating


def detect_email_column(fieldnames: List[str]) -> Optional[str]:
    """Look for a column whose name contains 'mail' or 'email'
    (case-insensitive), preferring an exact 'email' match."""
    if not fieldnames:
        return None
    lowered = {name: name.lower() for name in fieldnames}
    for name, low in lowered.items():
        if low == "email":
            return name
    for name, low in lowered.items():
        if "email" in low or "mail" in low:
            return name
    return None


def parse_csv(path: str, email_column: Optional[str] = None) -> List[Recipient]:
    """Parse a recipient CSV file into a list of Recipient records.

    Raises CsvParseError if the file has no rows, or no email column
    can be found/matches `email_column`.
    """
    with open(path, "r", encoding="utf-8-sig", newline="") as f:  # utf-8-sig strips BOM
        content = f.read()

    reader = csv.DictReader(io.StringIO(content))
    if reader.fieldnames is None:
        raise CsvParseError(f"{path}: no header row found")

    fieldnames = [fn.strip() for fn in reader.fieldnames]
    reader.fieldnames = fieldnames

    col = email_column or detect_email_column(fieldnames)
    if col is None:
        raise CsvParseError(
            f"{path}: could not auto-detect an email column "
            f"(looked for a column containing 'mail' or 'email' in "
            f"{fieldnames}); pass --email-column explicitly"
        )
    if col not in fieldnames:
        raise CsvParseError(f"{path}: column '{col}' not found in header {fieldnames}")

    recipients: List[Recipient] = []
    idx = 0
    for raw_row in reader:
        # Skip fully blank rows (all values empty/None after stripping).
        values = [(v or "").strip() for v in raw_row.values()]
        if not any(values):
            continue

        row = {k: (v or "").strip() for k, v in raw_row.items()}
        email = row.get(col, "").strip()
        if not email:
            # Row present but no email value -- skip, don't crash the
            # whole campaign over one bad row.
            continue

        recipients.append(Recipient(row_index=idx, email=email, data=row))
        idx += 1

    if not recipients:
        raise CsvParseError(f"{path}: no valid recipient rows found")

    return recipients
