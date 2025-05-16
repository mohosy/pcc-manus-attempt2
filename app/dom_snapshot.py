from playwright.async_api import Page, TimeoutError

MAX_CHARS = 4000  # keep payload under token limits

async def snapshot_dom(page: Page) -> str:
    """
    Return a trimmed, text-only dump of the visible DOM.

    Priority:
      1. Whatever’s inside the final Manus answer div (.MarkdownProse)
      2. Otherwise the whole Body (during loading / spinner)
    """
    # try the answer block first
    try:
        await page.wait_for_selector("div.MarkdownProse", timeout=2_000)
        dom_text = await page.inner_text("div.MarkdownProse")
    except TimeoutError:
        dom_text = await page.inner_text("body")

    dom_text = " ".join(dom_text.split())          # collapse whitespace
    return dom_text[:MAX_CHARS]
