from callmoves.docs import html_to_text, load_doc

RELEASE = """<!DOCTYPE html><html><head><title>Q3 Results</title><style>td { color: red }</style></head>
<body><!-- tracking --><script>var x = 1 &lt; 2;</script>
<h1>ACME Reports Third Quarter 2026 Results</h1>
<p>Revenue of <b>$21.9&nbsp;billion</b>, up 4% year over year.</p>
<table><tr><td>Net income</td><td>$</td><td>21,870</td></tr>
<tr><td>Diluted EPS</td><td>$</td><td>1.66</td></tr></table>
<div>Guidance &amp; outlook<br/>unchanged.</div></body></html>"""


def test_html_to_text_strips_markup_and_keeps_table_cells_apart():
    text = html_to_text(RELEASE)
    lines = text.split("\n")
    assert lines[0] == "ACME Reports Third Quarter 2026 Results"
    assert any(l.startswith("Revenue of $21.9 billion") and l.endswith("up 4% year over year.") for l in lines)
    assert "Net income $ 21,870" in lines and "Diluted EPS $ 1.66" in lines
    assert "Guidance & outlook" in lines and "unchanged." in lines
    for gone in ("Q3 Results", "color: red", "tracking", "var x", "<"):
        assert gone not in text
    assert "\n\n\n" not in text and "  " not in text


def test_load_doc_drops_nul_characters(tmp_path):
    p = tmp_path / "prior.txt"
    p.write_text("Revenue\x00 was $3.1 billion.")
    assert load_doc(p) == "Revenue was $3.1 billion."
