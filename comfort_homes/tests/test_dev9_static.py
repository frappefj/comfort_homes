from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]


def test_no_public_button_can_swallow_a_click_silently():
    for name in ("hp_webform_submit.js", "loan_application_duplicate_check.js"):
        source = (ROOT / "public/js" / name).read_text()
        assert 'toggle("disabled"' not in source
        assert "b.disabled = !" not in source and "button.disabled = blocked" not in source
    script = json.loads((ROOT / "fixtures/web_form.json").read_text())[0]["client_script"]
    assert "button.disabled = blocked" not in script and 'toggle("disabled"' not in script
    assert script.count("unified submit coordinator dev9") == 1 and "coordinator dev8" not in script
