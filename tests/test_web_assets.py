from __future__ import annotations

import json
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "src" / "spacefinder" / "web"


def test_index_has_accessibility_fallbacks() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")

    assert 'lang="ru"' in html
    assert "<noscript>" in html
    assert 'name="spacefinder-token"' in html
    assert 'aria-live="polite"' in html
    assert "<script>" not in html.lower()


def test_app_js_avoids_html_injection_and_has_timeout() -> None:
    script = (WEB / "app.js").read_text(encoding="utf-8")

    assert "innerHTML" not in script
    assert "insertAdjacentHTML" not in script
    assert "document.write" not in script
    assert "AbortController" in script
    assert "aria-busy" in script


def test_translation_keys_used_by_ui_exist() -> None:
    script = (WEB / "app.js").read_text(encoding="utf-8")
    translations = json.loads((WEB / "i18n" / "ru.json").read_text(encoding="utf-8"))
    used = set(re.findall(r"\bt\(\s*[\"']([^\"']+)[\"']", script))

    assert used
    assert used <= set(translations)


def test_styles_have_focus_and_reduced_motion() -> None:
    css = (WEB / "style.css").read_text(encoding="utf-8")

    assert ":focus-visible" in css
    assert "prefers-reduced-motion" in css
