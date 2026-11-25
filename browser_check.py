"""Run from any directory. Uses only disposable data, never the working database."""

from pathlib import Path
from urllib.parse import urlsplit
import secrets
from playwright.sync_api import sync_playwright
from live_server import live_server, ROOT

OUTPUT = ROOT / "tests" / "artifacts"
OUTPUT.mkdir(exist_ok=True)

with live_server() as (base, admin_password), sync_playwright() as p:
    browser = p.chromium.launch(args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.emulate_media(reduced_motion="reduce")
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base)
    page.screenshot(path=str(OUTPUT / "desktop.png"), full_page=True)
    assert page.locator("h1").count() == 1
    assert page.locator("img").evaluate_all(
        "(imgs)=>imgs.every(i=>i.complete&&i.naturalWidth>0)"
    )
    # All local links, static files and fragments on the public site.
    for href in set(
        page.locator("a[href],link[href],script[src],img[src]").evaluate_all(
            '(nodes)=>nodes.map(n=>n.getAttribute("href")||n.getAttribute("src"))'
        )
    ):
        if href.startswith("/"):
            url = urlsplit(href)
            assert page.request.get(base + url.path).status < 400, href
            if url.fragment:
                assert page.locator('[id="' + url.fragment + '"]').count(), href
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    for width in [320, 390, 768, 1024, 1440]:
        page.set_viewport_size({"width": width, "height": 900})
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=str(OUTPUT / "mobile.png"), full_page=True)
    page.get_by_role("button", name="Меню").click()
    assert page.locator("#navigation").is_visible()
    page.get_by_role("link", name="О проекте", exact=True).first.click()
    page.locator("#text-size").click()
    assert "large-text" in page.locator("body").get_attribute("class")
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    for path in ["/register", "/login", "/recover", "/privacy"]:
        assert page.goto(base + path).status == 200
        assert page.reload().status == 200
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), path
    # Real registration and restore on a temporary database.
    page.goto(base + "/register")
    password = secrets.token_urlsafe(24)
    fields = {
        "name": "Тестовый Участник",
        "age": "23",
        "city": "Казань",
        "education": "Высшее",
        "phone": "+0 000 000 00 00",
        "email": "browser@example.org",
        "experience": "Нет опыта",
        "motivation": "Тестовая заявка",
        "password": password,
    }
    for field, value in fields.items():
        page.locator('[name="' + field + '"]').fill(value)
    page.locator("[name=consent]").check()
    page.locator("form button").click()
    code = page.locator(".recovery-code").inner_text()
    assert code
    page.get_by_role("link", name="Перейти в кабинет").click()
    assert page.reload().status == 200
    for tab in [
        "Программа",
        "Расписание",
        "Материалы",
        "Практика",
        "Документы",
        "Профиль",
    ]:
        page.get_by_role("tab", name=tab, exact=True).click()
        assert page.reload().status == 200
        assert page.locator("[role=tab][aria-selected=true]").inner_text() == tab
    assert page.request.get(base + "/admin").status == 403
    page.locator('form[action="/logout"] button').click()
    page.goto(base + "/recover")
    page.locator("[name=email]").fill("browser@example.org")
    page.locator("[name=code]").fill(code)
    page.locator("[name=password]").fill(secrets.token_urlsafe(24))
    page.locator("form button").click()
    assert page.locator(".recovery-code").inner_text() != code
    page.goto(base + "/account")
    page.locator('form[action="/logout"] button').click()
    page.goto(base + "/login")
    page.locator("[name=email]").fill("admin@local.test")
    page.locator("[name=password]").fill(admin_password)
    page.locator("form button").click()
    page.wait_for_url(lambda url: urlsplit(url).path == "/admin")
    # Create content, cancel deletion, verify button is still usable, then delete.
    page.get_by_role("tab", name="Расписание и материалы", exact=True).click()
    page.locator("#content > form [name=title]").fill("Тестовое занятие")
    page.locator("#content > form button").click()
    page.wait_for_url(lambda url: urlsplit(url).path == "/admin")
    page.get_by_role("tab", name="Расписание и материалы", exact=True).click()
    page.locator("#content details summary").click()
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator(".delete-form button").click()
    assert page.locator(".delete-form button").is_enabled()
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator(".delete-form button").click()
    page.wait_for_url(lambda url: urlsplit(url).path == "/admin")
    page.get_by_role("tab", name="Расписание и материалы", exact=True).click()
    assert page.locator("#content details").count() == 0
    assert not errors, errors
    browser.close()
print(
    "PASS: local resources, links/fragments, 5 viewport widths, forms, auth, recovery, tabs/reload, permissions, delete cancellation."
)
