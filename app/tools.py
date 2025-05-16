"""
toolbox the LLM can invoke.
────────────────────────────
allowed actions:
  • click       – click a DOM element
  • type        – fill a field (no implicit “Enter”)
  • wait_for    – wait until selector is visible
  • ask_user    – bubble a question back to the terminal
  • done        – return final markdown payload to CLI
  • navigate    – (kept for future use) blocked unless URL is manus.im/*
"""

import re
import json
from typing import Any, Dict, Optional
from playwright.async_api import Page, TimeoutError

# ── JSON schema that goes into the GPT system prompt ─────────────────────────
TOOL_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["navigate", "click", "type", "wait_for", "ask_user", "done"],
            },
            "url": {"type": "string"},         # for navigate
            "selector": {"type": "string"},    # for click / type / wait_for
            "text": {"type": "string"},        # for type
            "timeout": {"type": "integer"},    # for wait_for
            "question": {"type": "string"},    # for ask_user
            "payload": {"type": "string"},     # for done
        },
        "required": ["action"],
        "additionalProperties": False,
    },
    indent=2,
)

# strict whitelist for navigation (gpt must stay on manus.im/app)
NAV_WHITELIST = re.compile(r"^https://manus\.im")

# ── executor ─────────────────────────────────────────────────────────────────
class ToolExecutor:
    """executes low-level browser actions on behalf of the LLM planner"""

    def __init__(self, page: Page):
        self.page = page

    async def run(self, call: Dict[str, Any]) -> Optional[str]:
        act = call["action"]

        # ----- NAVIGATE -------------------------------------------------------
        if act == "navigate":
            url = call["url"]
            if not NAV_WHITELIST.match(url):
                raise ValueError(f"blocked navigate to {url}")
            await self.page.goto(url, wait_until="domcontentloaded")
            return None

        # ----- CLICK ----------------------------------------------------------
        if act == "click":
            await self.page.click(call["selector"])
            return None

        # ----- TYPE -----------------------------------------------------------
        if act == "type":
            await self.page.fill(call["selector"], call["text"])
            # no implicit “Enter” — gpt must send a separate click action
            return None

        # ----- WAIT_FOR -------------------------------------------------------
        if act == "wait_for":
            try:
                await self.page.wait_for_selector(
                    call["selector"],
                    timeout=call.get("timeout", 10_000),  # default 10 s
                    state="visible",
                )
            except TimeoutError:
                pass  # swallow timeouts so gpt can decide what to do next
            return None

        # ----- ASK_USER -------------------------------------------------------
        if act == "ask_user":
            return f"_ASK_USER_::{call['question']}"

        # ----- DONE -----------------------------------------------------------
        if act == "done":
            return call.get("payload", "")

        raise ValueError(f"unknown tool action: {act}")
