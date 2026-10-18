"""Reference documents: the earnings release (and optionally the prior quarter's) as plain text."""
from __future__ import annotations

import re
from pathlib import Path


def pdf_to_text(path: Path) -> str:
    from pypdf import PdfReader

    text = "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def load_doc(path: Path) -> str:
    return pdf_to_text(path) if path.suffix.lower() == ".pdf" else path.read_text()
