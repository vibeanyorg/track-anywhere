from __future__ import annotations

from pathlib import Path


def test_no_raw_human_dict_fallback_in_renderer():
    text = Path("cli/track_anywhere_cli/renderers.py").read_text()

    assert "return data" not in text
