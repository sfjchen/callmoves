"""Reference documents: the earnings release (and optionally the prior quarter's) as plain text."""
from __future__ import annotations

import html
import re
from pathlib import Path


def pdf_to_text(path: Path) -> str:
    from pypdf import PdfReader

    text = "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def html_to_text(raw: str) -> str:
    """Flattens an HTML earnings release to plain text with the stdlib: block tags become line breaks, every other
    tag a space, so a table cell `$` next to `21,870` reads `$ 21,870`, which novelty already matches."""
    text = re.sub(r"<!--.*?-->", " ", raw, flags=re.S)
    text = re.sub(r"<(script|style|head)\b.*?</\1\s*>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</(p|div|h[1-6]|li|tr|table|blockquote)\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def load_doc(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return pdf_to_text(path)
    if suffix in (".html", ".htm"):
        return html_to_text(path.read_text(errors="replace"))
    return path.read_text()
