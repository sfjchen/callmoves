"""Checks that the web's copy of the theme regexes, which /themes uses to group moments, matches run.THEMES."""
import json
from pathlib import Path

from callmoves.run import THEMES

WEB_THEMES = Path(__file__).parents[2] / "web" / "src" / "lib" / "themes.json"


def test_web_theme_regexes_match_the_pipeline():
    assert json.loads(WEB_THEMES.read_text()) == THEMES
