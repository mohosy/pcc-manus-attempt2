import os
import json
from typing import Any, Dict, List

from playwright.async_api import async_playwright, TimeoutError, Page, Browser
from browserbase import Browserbase
from openai import AsyncOpenAI
from rich.console import Console

from .dom_snapshot import snapshot_dom
from .tools import ToolExecutor, TOOL_SCHEMA

# ────────────────────────────────────────────────────────────────────────────
# ENV + CONSTANTS   (⚠️  move creds to .env in prod)
# ────────────────────────────────────────────────────────────────────────────
MANUS_EMAIL = "pccagent18@gmail.com"
MANUS_PW    = "thisisforpcc"
BROWSERBASE_PROJECT_ID = os.getenv("BROWSERBASE_PROJECT_ID")
BROWSERBASE_API_KEY    = os.getenv("BROWSERBASE_API_KEY")
OPENAI_API_KEY         = os.getenv("OPENAI_API_KEY")

client  = AsyncOpenAI(api_key=OPENAI_API_KEY)
console = Console()

# DEBUG=0 → silent | 1 → normal | 2 → very loud
DEBUG_LEVEL = int(os.getenv("DEBUG", "0"))

def dbg(msg: str, level: int = 1):
    if DEBUG_LEVEL >= level:
        console.log(f"[bold cyan][DBG][/bold cyan] {msg}")

# ────────────────────────────────────────────────────────────────────────────
# auto‑login helper
# ────────────────────────────────────────────────────────────────────────────

GOOGLE_BTN_XPATH = "//button[contains(normalize-space(.), 'Sign up with Google') or contains(normalize-space(.), 'Continue with Google')]"
SIGN_IN_LINK     = "text=Already have an account? Sign in"
RUN_BTN          = "button:has-text('Run')"

async def auto_login(page: Page):
    """Log into Manus via Google (or fallback) until editor appears."""

    dbg("[login] waiting for initial load …")
    try:
        await page.wait_for_load_state("load", timeout=10_000)
    except TimeoutError:
        dbg("[login] load event timeout — continuing", 2)

    # switch from sign‑up to sign‑in if needed
    if await page.locator(SIGN_IN_LINK).count():
        dbg("[login] sign‑up page detected → clicking sign‑in link …")
        await page.click(SIGN_IN_LINK)
        try:
            await page.wait_for_load_state("load", timeout=8_000)
        except TimeoutError:
            dbg("[login] sign‑in load timeout — continuing", 2)

    # already in editor?
    if await page.locator(RUN_BTN).count():
        dbg("[login] already authenticated — editor ready ✅")
        return

    # google oauth button
    if await page.locator(f"xpath={GOOGLE_BTN_XPATH}").count():
        dbg("[login] Google OAuth button found → starting flow …")
        await page.click(f"xpath={GOOGLE_BTN_XPATH}")
        popup = await page.context.wait_for_event("page")

        dbg("[login] → filling email …")
        await popup.fill("input[type='email']", MANUS_EMAIL)
        await popup.click("button:has-text('Next')")

        dbg("[login] → filling password …")
        await popup.wait_for_selector("input[type='password']", timeout=12_000)
        await popup.fill("input[type='password']", MANUS_PW)
        await popup.click("button:has-text('Next')")

        try:
            await popup.wait_for_close(timeout=30_000)
        except TimeoutError:
            dbg("[login] popup close timeout — continuing", 2)
        await page.wait_for_timeout(2000)

    # final editor check
    if await page.locator(RUN_BTN).count():
        dbg("[login] login complete — editor ready ✅")
        return

    dbg("[login] auth flow finished but editor not detected — proceeding unauthenticated", 2)

# ────────────────────────────────────────────────────────────────────────────
# GPT planner helper (OpenAI ≥ 1.0)
# ────────────────────────────────────────────────────────────────────────────

async def ask_gpt(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    dbg("[gpt] calling gpt‑4o …")
    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        tools=[{
            "type": "function",
            "function": {
                "name": "browser_tool",
                "description": "DOM interactor for Manus app",
                "parameters": json.loads(TOOL_SCHEMA),
            },
        }],
        temperature=0,
    )
    call = resp.choices[0].message.tool_calls[0]
    dbg(f"[gpt] tool_call → {call.function.name}")
    dbg(f"[gpt] args → {call.function.arguments}", 2)
    return json.loads(call.function.arguments)

# ────────────────────────────────────────────────────────────────────────────
# orchestrate() — exposed to CLI
# ────────────────────────────────────────────────────────────────────────────

async def orchestrate(goal: str) -> str:
    dbg("[main] spinning up Browserbase session …")
    bb = Browserbase(api_key=BROWSERBASE_API_KEY)
    session = bb.sessions.create(project_id=BROWSERBASE_PROJECT_ID)

    async with async_playwright() as pw:
        dbg("[main] connecting Playwright over CDP …")
        browser: Browser = await pw.chromium.connect_over_cdp(session.connect_url)
        try:
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = context.pages[0] if context.pages else await context.new_page()

            dbg("[main] navigating to /login …")
            await page.goto("https://manus.im/login", wait_until="domcontentloaded")
            await auto_login(page)

            executor = ToolExecutor(page)
            messages: List[Dict[str, Any]] = [
                {"role": "system", "content": "You are the ops brain that drives Manus.ai UI via limited tools."},
                {"role": "user",   "content": goal},
            ]

            step = 0
            while True:
                step += 1
                dom_text = await snapshot_dom(page)
                dbg(f"[step {step}] DOM length: {len(dom_text)} chars")
                messages.append({"role": "user", "name": "DOM", "content": dom_text})

                call = await ask_gpt(messages)
                dbg(f"[step {step}] gpt action → {call['action']}")

                if call["action"] == "ask_user":
                    user_input = input(f"{call['question']} › ")
                    await page.fill("textarea", user_input)
                    await page.click("button:has-text('Run')")
                    continue

                if call["action"] == "done":
                    dbg("[main] done action received — returning payload …")
                    return call.get("payload", "")

                await executor.run(call)
                dbg(f"[step {step}] executed tool ✅", 2)
                messages.append({"role": "assistant", "name": "tool", "content": json.dumps(call)})

        finally:
            dbg("[main] closing browser …")
            await browser.close()
            dbg("[main] deleting Browserbase session …")
            try:
                bb.sessions._delete(session.id, cast_to=dict)
                dbg("[main] session deleted ✅")
            except Exception as e:
                console.log(f"[yellow][warn][/yellow] failed to delete Browserbase session: {e}")