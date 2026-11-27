"""Open the production Sources directory in Chrome and inspect the FRSE row."""
import shutil
from playwright.sync_api import expect, sync_playwright

with sync_playwright() as p:
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    assert chrome, "Hosted Chrome is missing"
    browser = p.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
    page = browser.new_page()
    page.goto("https://youthopps.org/sources/", wait_until="domcontentloaded")
    form = page.locator("form.index-search")
    field = form.locator('input[name="q"]')
    field.fill("FRSE")
    form.locator('button[type="submit"]').click()
    card = page.locator("article.publisher-card").filter(
        has=page.locator('a[href="/sources/publisher/pl-frse/"]'))
    expect(card).to_have_count(1, timeout=30000)
    expect(card.locator(".source-status.active")).to_have_text("Success")
    card.locator('a[href="/sources/publisher/pl-frse/"]').click()
    expect(page).to_have_url("https://youthopps.org/sources/publisher/pl-frse/")
    expect(page.locator("article.publisher-card .source-status.active")).to_have_text("Success")
    print("Chrome confirmed the production Sources search and FRSE publisher page display the green Success state")
    browser.close()
