"""
{{variable}} template rendering, matching maillaunch.html's behavior:
a simple regex-based substitution from a data dict, column names are
matched case-insensitively, and unresolved placeholders are left
in place (rather than raising) so a bad template doesn't take down an
entire send -- callers can inspect the return value for warnings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

# Matches {{ key }} allowing optional surrounding whitespace. Extra
# surrounding braces beyond a single {{ }} pair are not part of a
# valid token and are left as literal text, e.g.
# "{{{{name}}}}" -> "{{" + "John" + "}}".
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_ \-]+?)\s*\}\}")


@dataclass
class RenderResult:
    text: str
    missing_keys: List[str] = field(default_factory=list)


def render(template_str: str, data: dict) -> RenderResult:
    # Case-insensitive lookup: build a lowercase-keyed view once.
    lower_data = {str(k).lower(): v for k, v in data.items()}
    missing: List[str] = []

    def replace(match: re.Match) -> str:
        key = match.group(1).strip()
        low_key = key.lower()
        if low_key in lower_data:
            return str(lower_data[low_key])
        missing.append(key)
        return match.group(0)  # leave placeholder untouched

    rendered = PLACEHOLDER_RE.sub(replace, template_str)
    return RenderResult(text=rendered, missing_keys=missing)
