"""Sign in manually in the server's private browser; never receive Google credentials."""
import argparse
import asyncio
import re
from pathlib import Path

from patchright.async_api import async_playwright

import config
from browser import launch_account_context, cookie_value


async def login(account: str, timeout: int):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", account):
        raise ValueError("Account name must contain 1-32 letters, numbers, underscores or hyphens")
    target = Path("accounts") / account
    # A new account stays hidden from the scheduler until its session is saved.
    browser_account = account if target.exists() else f".login-{account}"
    profile = Path("accounts") / browser_account
    profile.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        context = await launch_account_context(p, browser_account, headless=False)
        success = False
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://www.dola.com/chat", wait_until="domcontentloaded", timeout=60000)
            print("Open the private browser screen and complete login and verification yourself.", flush=True)
            deadline = asyncio.get_running_loop().time() + timeout
            while asyncio.get_running_loop().time() < deadline:
                if cookie_value(await context.cookies("https://www.dola.com"), "sessionid"):
                    success = True
                    break
                await asyncio.sleep(2)
            if not success:
                raise TimeoutError("Manual login timed out; run this command again to continue")
        finally:
            await context.close()
        if profile != target:
            profile.rename(target)
        print(f"Session saved for {account}. Close this screen and refresh Accounts.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("account")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    asyncio.run(login(args.account, args.timeout))
