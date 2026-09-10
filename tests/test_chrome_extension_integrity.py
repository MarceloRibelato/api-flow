import json
import re
from pathlib import Path

EXTENSION_DIR = Path(__file__).parent.parent.parent / "chrome-extension-front-recorder"

def test_manifest_validity():
    for manifest_name in ["manifest.json", "manifest-chrome.json", "manifest-firefox.json"]:
        manifest_path = EXTENSION_DIR / manifest_name
        assert manifest_path.exists(), f"{manifest_name} does not exist"
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data.get("default_locale") == "pt_BR", f"{manifest_name} must specify default_locale: pt_BR"
        assert data.get("name") == "__MSG_extName__"
        assert data.get("description") == "__MSG_extDesc__"

def test_locales_key_parity_and_non_empty():
    pt_path = EXTENSION_DIR / "_locales" / "pt_BR" / "messages.json"
    en_path = EXTENSION_DIR / "_locales" / "en" / "messages.json"

    assert pt_path.exists(), "pt_BR messages.json missing"
    assert en_path.exists(), "en messages.json missing"

    with open(pt_path, "r", encoding="utf-8") as f:
        pt_data = json.load(f)

    with open(en_path, "r", encoding="utf-8") as f:
        en_data = json.load(f)

    pt_keys = set(pt_data.keys())
    en_keys = set(en_data.keys())

    missing_in_en = pt_keys - en_keys
    missing_in_pt = en_keys - pt_keys

    assert not missing_in_en, f"Keys in pt_BR but missing in en: {missing_in_en}"
    assert not missing_in_pt, f"Keys in en but missing in pt_BR: {missing_in_pt}"

    for k, v in pt_data.items():
        assert "message" in v and len(v["message"].strip()) > 0, f"Empty message in pt_BR key: {k}"

    for k, v in en_data.items():
        assert "message" in v and len(v["message"].strip()) > 0, f"Empty message in en key: {k}"

def test_html_i18n_attributes_coverage():
    with open(EXTENSION_DIR / "_locales" / "pt_BR" / "messages.json", "r", encoding="utf-8") as f:
        locale_keys = set(json.load(f).keys())

    for html_name in ["popup.html", "options.html"]:
        html_path = EXTENSION_DIR / html_name
        content = html_path.read_text(encoding="utf-8")

        # Find all data-i18n attributes
        keys_found = re.findall(r'data-i18n(?:-placeholder|-title|-value)?=["\']([^"\']+)["\']', content)
        for key in keys_found:
            assert key in locale_keys, f"Key '{key}' in {html_name} not found in messages.json"

def test_no_remote_fonts_or_external_styles_in_html():
    for html_name in ["popup.html", "options.html"]:
        html_path = EXTENSION_DIR / html_name
        content = html_path.read_text(encoding="utf-8")
        assert "fonts.googleapis.com" not in content, f"Google Fonts found in {html_name}"
        assert "fonts.gstatic.com" not in content, f"Google Fonts static found in {html_name}"
        assert "@import" not in content, f"CSS @import found in {html_name}"
        # Verify no external <link> or <script> tags loading remote resources
        assert not re.search(r'<(?:link|script)[^>]+(?:src|href)=["\']https?://', content, re.IGNORECASE), f"Remote tag found in {html_name}"

def test_security_popup_js_safe_dom():
    popup_js = (EXTENSION_DIR / "popup.js").read_text(encoding="utf-8")
    assert "window.i18n" in popup_js
    assert "innerHTML" not in popup_js, "popup.js must not use innerHTML to prevent DOM-XSS risks"

def test_content_js_idempotency_and_safe_token():
    content_js = (EXTENSION_DIR / "content.js").read_text(encoding="utf-8")
    assert "__FLOW_RECORDER_CONTENT_ACTIVE__" in content_js, "content.js must have idempotency guard"
    assert "window.location.hostname.includes('flow')" not in content_js, "content.js must not use insecure hostname.includes"
    assert "document.title.toLowerCase().includes('flow')" not in content_js, "content.js must not use title.includes for token detection"
