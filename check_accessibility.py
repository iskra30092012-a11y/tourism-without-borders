"""Automated axe checks, on a temporary database and generated credentials."""

import json
from playwright.sync_api import sync_playwright
from live_server import live_server, ROOT

report = []
with live_server() as (base, password), sync_playwright() as p:
    browser = p.chromium.launch(args=["--no-sandbox"])
    # axe is injected by the test harness only, not loaded by the website.
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, bypass_csp=True)
    page.emulate_media(reduced_motion="reduce")

    def audit(name):
        page.add_script_tag(path=str(ROOT / "tests" / "axe.min.js"))
        result = page.evaluate(
            "async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})"
        )
        report.append(
            {
                "page": name,
                "violations": [
                    {"id": v["id"], "nodes": len(v["nodes"])}
                    for v in result["violations"]
                ],
            }
        )

    for path in ["/", "/register", "/login", "/recover", "/privacy"]:
        page.goto(base + path)
        audit(path)
    page.goto(base + "/login")
    page.locator("[name=email]").fill("admin@local.test")
    page.locator("[name=password]").fill(password)
    page.locator("form button").click()
    page.wait_for_url("**/admin")
    for tab in [
        "Заявки",
        "Расписание и материалы",
        "Посещаемость",
        "Сообщения",
        "Настройки",
    ]:
        page.get_by_role("tab", name=tab, exact=True).click()
        audit("/admin#" + tab)
    page.goto(base + "/account")
    for tab in [
        "Программа",
        "Расписание",
        "Материалы",
        "Практика",
        "Документы",
        "Профиль",
    ]:
        page.get_by_role("tab", name=tab, exact=True).click()
        audit("/account#" + tab)
    browser.close()
output = ROOT / "tests" / "artifacts"
output.mkdir(exist_ok=True)
(output / "accessibility-report.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2)
)
print(json.dumps(report, ensure_ascii=False, indent=2))
assert not any(item["violations"] for item in report)
