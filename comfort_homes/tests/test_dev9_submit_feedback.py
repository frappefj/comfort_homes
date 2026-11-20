"""Local Chromium regressions for the public submit feedback rules. Synthetic native-save model;
NOT Frappe site acceptance. Skipped when Playwright/Chromium is unavailable."""
from pathlib import Path
import pytest

sync_api = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]
URL = "http://flexi.test/loan-application-new/new"

HARNESS = """<!doctype html><html><body><form class="web-form"><input name="n" value="Synthetic"><button class="btn-next" type="submit">Next</button><button class="submit-btn" type="submit">Submit</button></form>
<script>
window.test={saves:0,lookups:0,alerts:[],msgs:[],delay:30,hang:false,exists:false};
window.__=s=>s; window.$=()=>({on(){}});
window.flexiValidateFinalFields=()=>{};
window.frappe={msgprint:m=>test.msgs.push(String(m.message||m)),show_alert:(m)=>test.alerts.push(String(m.message||m)),
 web_form:{fields_dict:{},events:{on:(_e,f)=>f()},get_value:k=>({custom_tin_number:"t",applicant_email_address:"a@b.invalid"}[k]),
  save(){if(window.saving)return false;window.saving=true;test.saves++;setTimeout(()=>{window.saving=false;frappe.web_form.handle_success({name:"X"});},test.delay);return false;},
  handle_success(){}},
 call(){test.lookups++;return test.hang?new Promise(()=>{}):new Promise(r=>setTimeout(()=>r({message:{exists:test.exists}}),test.delay));}};
</script><script src="/hp_webform_submit.js"></script></body></html>"""


@pytest.fixture()
def page():
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip("Chromium unavailable: %s" % exc)
        pg = browser.new_page()
        pg.clock.install()
        script = (ROOT / "public/js/hp_webform_submit.js").read_text()
        def route(r):
            u = r.request.url
            if u.endswith("/hp_webform_submit.js"):
                r.fulfill(body=script, content_type="text/javascript")
            else:
                r.fulfill(body=HARNESS, content_type="text/html")
        pg.route("http://flexi.test/**", route)
        pg.goto(URL)
        yield pg
        browser.close()


def settle(pg, ms=500):
    pg.clock.run_for(ms)
    pg.wait_for_timeout(50)


def test_click_while_busy_answers_visibly_and_button_is_never_disabled(page):
    page.evaluate("test.delay=300")
    page.click(".submit-btn")
    page.click(".submit-btn")  # second click lands while the first is checking
    assert page.evaluate("document.querySelector('.submit-btn').disabled") is False
    assert any("still being checked" in a for a in page.evaluate("test.alerts"))
    settle(page, 2000)
    assert page.evaluate("test.saves") == 1


def test_click_after_saved_says_already_sent(page):
    page.click(".submit-btn")
    settle(page, 1000)
    assert page.evaluate("flexiSubmit.phase()") == "saved"
    page.click(".submit-btn")
    assert any("already been sent" in m for m in page.evaluate("test.msgs"))
    assert page.evaluate("test.saves") == 1


def test_hung_lookup_times_out_visibly_and_allows_retry(page):
    page.evaluate("test.hang=true")
    page.click(".submit-btn")
    settle(page, 21000)
    assert page.evaluate("flexiSubmit.phase()") == "rejected"
    assert any("not been sent" in m for m in page.evaluate("test.msgs"))
    page.evaluate("test.hang=false")
    page.click(".submit-btn")
    settle(page, 1000)
    assert page.evaluate("test.saves") == 1


def test_stuck_native_saving_flag_gives_message_not_silence(page):
    page.evaluate("window.saving=true")
    page.click(".submit-btn")
    settle(page, 500)
    assert page.evaluate("flexiSubmit.phase()") == "rejected"
    assert any("still in progress" in m for m in page.evaluate("test.msgs"))
    assert page.evaluate("test.saves") == 0
