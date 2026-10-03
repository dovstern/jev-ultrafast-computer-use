"""Native labels remain usable when styled inputs are invisible."""

import pytest
from jev_ultrafast.browser import Browser, StalePage

from jev_ultrafast_computer_use.browser_adapter import BrowserAdapter

HTML = """<!doctype html><title>Styled native controls</title>
<style>
body {margin: 30px}
label {display: block; width: 220px; height: 45px; margin-bottom: 15px; background: #ddd}
input.hidden {opacity: 0; position: absolute; width: 1px; height: 1px}
</style>
<input class="hidden" id="check" type="checkbox"><label for="check">Refundable</label>
<input class="hidden" id="radio" type="radio" name="stops"><label for="radio">One stop</label>
<input class="hidden" id="disabled" type="radio" disabled><label for="disabled">Unavailable</label>
<input class="hidden" id="aria-disabled" type="checkbox" aria-disabled="true">
<label for="aria-disabled">Not available</label>
<label>Visible <input id="visible" type="checkbox"></label>
<script>
window.changes = 0;
document.addEventListener('change', () => window.changes++);
</script>"""


@pytest.fixture
def browser():
    original = Browser("about:blank")
    adapter = BrowserAdapter(original)
    try:
        frame = original.call("Page.getFrameTree")["frameTree"]["frame"]["id"]
        original.call("Page.setDocumentContent", frameId=frame, html=HTML)
        yield adapter
    finally:
        original.close()


@pytest.mark.parametrize("label, input_id, role", [("Refundable", "check", "checkbox"), ("One stop", "radio", "radio")])
def test_visible_label_click_updates_hidden_native_input_once(browser, label, input_id, role):
    page = browser.observe(screenshot=False)
    actions = [a for a in page["actions"] if a["label"] == label]
    assert len(actions) == 1
    action = actions[0]
    assert action["role"] == role
    assert action["checked"] == "false"
    assert action["kind"] == "click"
    assert browser.fresh(page)
    assert browser.fresh(page, action)
    browser.act(action, page)
    assert browser.evaluate(f"document.getElementById('{input_id}').checked") is True
    assert browser.evaluate("window.changes") == 1
    updated = browser.observe(screenshot=False)
    assert next(a for a in updated["actions"] if a["label"] == label)["checked"] == "true"
    assert page["fingerprint"] != updated["fingerprint"]


def test_disabled_labels_are_omitted_and_visible_inputs_are_not_duplicated(browser):
    page = browser.observe(screenshot=False)
    assert not any(a["label"] in {"Unavailable", "Not available"} for a in page["actions"])
    assert len([a for a in page["actions"] if a["label"] == "Visible"]) == 1


@pytest.mark.parametrize("change", ["disable", "retarget", "cover"])
def test_label_changes_are_rejected_before_any_input(browser, change):
    page = browser.observe(screenshot=False)
    action = next(a for a in page["actions"] if a["label"] == "Refundable")
    if change == "disable":
        browser.evaluate("document.getElementById('check').disabled = true")
    elif change == "retarget":
        browser.evaluate("document.querySelector('label[for=check]').htmlFor = 'radio'")
    else:
        browser.evaluate(
            "const cover=document.createElement('div');"
            "cover.style.cssText='position:fixed;inset:0;z-index:1000';document.body.append(cover)"
        )
    with pytest.raises(StalePage):
        browser.act(action, page)
    assert browser.evaluate("window.changes") == 0
    assert browser.evaluate("document.getElementById('check').checked") is False
    assert browser.evaluate("document.getElementById('radio').checked") is False


def test_covered_controls_are_absent_while_exposed_grid_cells_remain_usable(browser):
    browser.evaluate("""document.body.innerHTML = `
      <button id="covered" style="position:absolute;top:40px;left:40px;width:100px;height:40px"
        onclick="window.coveredClicks=(window.coveredClicks||0)+1">Covered</button>
      <div style="position:absolute;top:40px;left:40px;width:100px;height:40px;z-index:9"></div>
      <div role="gridcell" aria-label="April 30" style="position:absolute;top:110px;left:40px;width:100px;height:40px"
        onclick="window.dayClicks=(window.dayClicks||0)+1">30</div>
    `""")
    raw = browser._browser.observe(screenshot=False)
    assert any(a["label"] == "Covered" for a in raw["actions"])
    page = browser.observe(screenshot=False)
    assert not any(a["label"] == "Covered" for a in page["actions"])
    day = next(a for a in page["actions"] if a["label"] == "April 30")
    assert day["role"] == "gridcell"
    assert browser.fresh(page)
    browser.act(day, page)
    assert browser.evaluate("window.dayClicks") == 1
    assert browser.evaluate("window.coveredClicks || 0") == 0
