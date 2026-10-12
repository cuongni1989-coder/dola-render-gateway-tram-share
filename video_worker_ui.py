"""Video generation worker with operator-completed verification."""
import asyncio
import json
import re
import sys
import time
from pathlib import Path

from patchright.async_api import async_playwright


import config
from browser import cookie_value, launch_account_context
from dola_client import CREDIT_FAIL_PATTERN, CreditError
from video_worker import POLL_JS, RiskControlError, _download, extract_unwatermarked_url

# Daily limit pattern matching response text
DAILY_LIMIT_PATTERN = re.compile(
    r"動画生成の\s*1日あたりの上限|每日(?:视频|影片)?生成.*(?:上限|限额|额度)|"
    r"daily.*(?:limit|quota)|(?:limit|quota).*per\s*day",
    re.IGNORECASE,
)


class AccountLimitedError(Exception):
    """Account reached daily video generation limit."""


class CreditInsufficientError(Exception):
    """Insufficient points prior to generation."""


VIDEO_BTN = "text=動画を作成"          # Entry point button in ja-JP locale
CAPTCHA_FRAME_KEY = "bdcaptcha.html"   # Captcha verifycenter iframe


# Read-only balance pre-check from recent conversations
BALANCE_JS = r"""
async ({msToken, fp}) => {
  const params = new URLSearchParams({
    version_code: "20800", language: "ja", device_platform: "web",
    doubao_device_platform: "web", aid: "495671", real_aid: "495671",
    pkg_type: "release_version", pc_version: "3.32.62", doubao_pc_version: "3.32.62",
    region: "JP", sys_region: "JP", samantha_web: "1", web_platform: "browser",
    "use-olympus-account": "1", web_tab_id: crypto.randomUUID(),
  });
  if (msToken) params.set("msToken", msToken);
  if (fp) params.set("fp", fp);
  const headers = {
    "Content-Type": "application/json; encoding=utf-8",
    "agw-js-conv": "str", "Accept": "*/*",
  };
  const recent = await fetch("/im/chain/recent_conv?" + params.toString(), {
    method: "POST", headers,
    body: JSON.stringify({
      cmd: 3200,
      uplink_body: {pull_recent_conv_chain_uplink_body: {
        limit: 20, message_count_per_conv: 10, api_version: 1, conv_version: 0,
        direction: 3,
        option: {not_need_message: false, need_complete_conversation: true,
          need_coco_conversation: true, need_coco_bot: true,
          need_pc_pin_chain: true, pc_pin_query_type: 0},
      }},
      sequence_id: crypto.randomUUID(), channel: 2, version: "1",
    }), credentials: "include",
  });
  if (!recent.ok) return {ok: false, texts: []};
  const recentData = await recent.json();
  const body = recentData.downlink_body || {};
  const down = body.pull_recent_conv_chain_downlink_body || {};
  const cells = down.cells || [];
  const ids = cells.map(c => (c.conversation || {}).conversation_id || c.id)
    .filter(Boolean).slice(0, 10);
  if (!ids.length) return {ok: true, texts: []};

  const batch = await fetch("/im/chain/batch_single?" + params.toString(), {
    method: "POST", headers,
    body: JSON.stringify({
      cmd: 3101,
      uplink_body: {batch_pull_singe_chain_uplink_body: {
        conversation_type: 3, direction: 3, limit: 1,
        params: ids.map(conversation_id => ({conversation_id})),
        evaluate_ab_params: "", evaluate_common_params: "", ext: {},
      }},
      sequence_id: crypto.randomUUID(), channel: 2, version: "1",
    }), credentials: "include",
  });
  if (!batch.ok) return {ok: true, texts: []};
  const data = await batch.json();
  const texts = [];
  const seen = new Set();
  const walk = (v) => {
    if (typeof v === "string") {
      if ((v.includes("ポイント") || v.includes("积分") || v.toLowerCase().includes("points")
        || v.includes("上限") || v.includes("limit")) && v.length < 1200 && !seen.has(v)) {
        seen.add(v); texts.push(v);
      }
      try {
        const t = v.trim();
        if (t.startsWith("{") || t.startsWith("[")) walk(JSON.parse(t));
      } catch (e) {}
      return;
    }
    if (!v || typeof v !== "object") return;
    if (Array.isArray(v)) { for (const x of v) walk(x); return; }
    for (const x of Object.values(v)) walk(x);
  };
  walk(data);
  return {ok: true, texts: texts.slice(-80)};
}
""";


def find_captcha_frame(page):
    for f in page.frames:
        if CAPTCHA_FRAME_KEY in f.url:
            return f
    return None





async def attach_reference_images(page, image_paths: list[str]) -> None:
    """Uploads reference images through native file input and waits for TOS upload."""
    if not image_paths:
        return
    file_input = page.locator('input[type="file"]').first
    await file_input.wait_for(state="attached", timeout=10000)
    events = []

    def on_response(response):
        url = response.url
        if "/alice/resource/prepare_upload" in url or "/upload/v1/" in url:
            events.append((response.status, url))

    page.on("response", on_response)
    try:
        await file_input.set_input_files(image_paths)
        expected = len(image_paths)
        deadline = time.time() + max(60, expected * 20)
        while time.time() < deadline:
            prepare_count = sum("/alice/resource/prepare_upload" in url and 200 <= status < 300
                                for status, url in events)
            tos_count = sum("/upload/v1/" in url and 200 <= status < 300
                            for status, url in events)
            # Wait for thumbnails and TOS completion before sending
            thumb_count = await page.locator('img[alt]').count()
            if prepare_count >= expected and tos_count >= expected and thumb_count >= expected:
                await page.wait_for_timeout(800)
                print(f"[upload] Reference images uploaded: {expected} image(s)", flush=True)
                return
            await page.wait_for_timeout(250)
        raise TimeoutError(
            f"Reference image upload timeout: prepare={prepare_count}/{expected}, tos={tos_count}/{expected}"
        )
    finally:
        page.remove_listener("response", on_response)







_BALANCE_PATTERNS = (
    re.compile(r"(?:本日は|今日(?:还剩|剩余)?|今天).*?(\d+)\s*(?:ポイント|积分|points?)", re.I),
    re.compile(r"(?:remaining|left)\s*[:：]?\s*(\d+)\s*points?", re.I),
    re.compile(r"(?:还剩|剩余|还有)\s*(\d+)\s*(?:积分|点)", re.I),
)


def _parse_balance_texts(texts: list[str]) -> tuple[int | None, bool, str]:
    for text in texts:
        if DAILY_LIMIT_PATTERN.search(text):
            return None, True, text
    for text in texts:
        for pattern in _BALANCE_PATTERNS:
            match = pattern.search(text)
            if match:
                return int(match.group(1)), False, text
    return None, False, ""


async def _preflight_balance(page, ms_token: str, fp: str, required: int) -> dict:
    """Reads known credit balance from chat history."""
    try:
        result = await asyncio.wait_for(page.evaluate(
            BALANCE_JS, {"msToken": ms_token, "fp": fp}), timeout=30)
        balance, daily_limited, source = _parse_balance_texts(result.get("texts", []))
        if daily_limited:
            raise AccountLimitedError(f"Account daily generation limit: {source[:120]}")
        if balance is not None and balance < required:
            raise CreditInsufficientError(
                f"Insufficient points: current {balance}, required {required} (source: {source[:120]})"
            )
        return {"balance": balance, "source": source}
    except (AccountLimitedError, CreditInsufficientError):
        raise
    except Exception as e:
        print(f"  Balance pre-check indeterminate (proceeding with submit): {str(e)[:120]}", flush=True)
        return {"balance": None, "source": ""}


async def poll_conversation(account: str, page, context, conversation_id: str,
                            timeout: int, on_poll=None, on_balance=None) -> dict:
    """Polls accepted conversation for video completion."""
    cookies = await context.cookies("https://www.dola.com")
    ms_token, fp = cookie_value(cookies, "msToken"), cookie_value(cookies, "s_v_web_id")
    start = time.time()
    last_callback = 0.0
    while time.time() - start < timeout:
        await asyncio.sleep(5)
        try:
            poll = await asyncio.wait_for(page.evaluate(
                POLL_JS, {"conversationId": conversation_id, "msToken": ms_token, "fp": fp}), timeout=30)
        except Exception as e:
            print(f"  Polling exception: {e}", flush=True)
            continue
        now = time.time()
        if on_poll and now - last_callback >= 30:
            on_poll(now)
            last_callback = now
        for text in poll.get("texts", []):
            balance, _, source = _parse_balance_texts([text])
            if balance is not None and on_balance:
                on_balance(balance, source)
            if DAILY_LIMIT_PATTERN.search(text):
                raise AccountLimitedError(f"Account daily limit reached: {text[:120]}")
            if CREDIT_FAIL_PATTERN.search(text):
                raise CreditError(f"Insufficient quota: {text[:80]}")
        if poll.get("videos"):
            video_models = poll.get("videoModels", [])
            url = extract_unwatermarked_url(
                video_models[0] if video_models else "", poll["videos"][0])
            print(f"[{account}] Completed! Downloading (unwatermarked priority)...", flush=True)
            local = await _download(url, account)
            print(f"[{account}] Downloaded {local} ({local.stat().st_size / 1e6:.1f} MB)", flush=True)
            return {"video_url": url, "local_path": str(local),
                    "conversation_id": conversation_id, "account": account}
        print(f"  ...Generating ({int(time.time() - start)}s)", flush=True)
    raise TimeoutError(f"No video generated within {timeout}s (conversation_id={conversation_id})")


async def resume_video(account: str, conversation_id: str, timeout: int,
                       on_poll=None, on_balance=None) -> dict:
    """Recovers accepted session after server restart without re-sending prompt."""
    async with async_playwright() as p:
        context = await launch_account_context(p, account, headless=False, use_extension=config.EXTENSION_ENABLED)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(f"https://www.dola.com/chat/{conversation_id}",
                            timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)
            return await poll_conversation(account, page, context, conversation_id, timeout, on_poll, on_balance)
        finally:
            await context.close()


async def generate_video(account: str, prompt: str, ratio: str = None,
                         duration: int = None, timeout: int = None,
                         model: str = "seedance_v2.0", use_extension: bool | None = None,
                         on_conversation_id=None, on_poll=None, on_balance=None,
                         reference_image_paths: list[str] | None = None) -> dict:
    """Full generation flow via UI automation."""
    timeout = timeout or config.VIDEO_TIMEOUT
    model_key = model.lower().replace("-", "_")
    if model_key in ("seedance_2.5", "seedance_v2.5", "seedance_25", "seedance_v25"):
        model_key = "seedance_v2.5"
    elif model_key in ("seedance_2.0", "seedance_v2.0", "seedance_20", "seedance_v20"):
        model_key = "seedance_v2.0"
    else:
        raise ValueError(f"Unsupported model: {model} (supported: seedance-2.0 / seedance-2.5)")
    if duration is not None and duration not in (10, 15, 30):
        raise ValueError("Dola supports durations of 10s, 15s, and 30s via extension")
    if use_extension is None:
        use_extension = config.EXTENSION_ENABLED
    if duration == 30 and not use_extension:
        raise ValueError("30s generation requires Dola30 extension enabled")
    # 30s videos require extended generation timeout
    if duration == 30:
        timeout = max(timeout, 1800)
    if reference_image_paths:
        timeout = max(timeout, config.REFERENCE_VIDEO_TIMEOUT)
    async with async_playwright() as p:
        context = await launch_account_context(
            p, account, headless=False if use_extension else None,
            use_extension=use_extension)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://www.dola.com/chat", timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)
            cookies = await context.cookies("https://www.dola.com")
            ms_token, fp = cookie_value(cookies, "msToken"), cookie_value(cookies, "s_v_web_id")
            await _preflight_balance(page, ms_token, fp, config.VIDEO_REQUIRED_POINTS)

            # ---- UI Submission ----
            await page.click(VIDEO_BTN)
            await page.wait_for_timeout(1500)
            if reference_image_paths:
                await attach_reference_images(page, reference_image_paths)
            # Select model in UI
            try:
                current_model = None
                for label in ("モデル 2.0高速", "モデル 2.5"):
                    loc = page.get_by_text(label, exact=True).first
                    if await loc.count() and await loc.is_visible():
                        current_model = loc
                        break
                if current_model is None:
                    current_model = page.get_by_text(re.compile(r"^モデル "), exact=False).first
                await current_model.click(timeout=5000)
                await page.wait_for_timeout(500)
                options = (("Dreamina Seedance 2.5",)
                           if model_key == "seedance_v2.5"
                           else ("Dreamina Seedance 2.0高速", "Dreamina Seedance 2.0", "Seedance2.0Fast"))
                selected = False
                for option_text in options:
                    loc = page.get_by_text(option_text, exact=False).first
                    if await loc.count() and await loc.is_visible():
                        await loc.click(timeout=5000)
                        selected = True
                        break
                if not selected:
                    raise RuntimeError("Model option not found")
                await page.wait_for_timeout(500)
            except Exception as e:
                raise RuntimeError(f"Failed to set model ({model_key}): {str(e)[:120]}") from e
            if ratio:
                try:
                    await page.click("text=比率", timeout=3000)
                    await page.wait_for_timeout(500)
                    await page.click(f"text={ratio}", timeout=3000)
                except Exception as e:
                    print(f"  (Failed to set ratio, using default: {str(e)[:80]})", flush=True)
            if duration:
                try:
                    await page.click(f"text={duration}s", timeout=3000)
                except Exception:
                    try:  # Open duration dropdown
                        await page.get_by_text(re.compile(r"^\d+s$")).first.click(timeout=3000)
                        await page.wait_for_timeout(500)
                        await page.click(f"text={duration}s", timeout=3000)
                    except Exception as e:
                        print(f"  (Failed to set duration, using default: {str(e)[:80]})", flush=True)
            box = await page.query_selector("textarea") or await page.query_selector('[contenteditable="true"]')
            await box.click()
            await page.keyboard.type(prompt, delay=100)
            await page.wait_for_timeout(600)
            await page.keyboard.press("Enter")
            print(f"[{account}] UI submitted prompt: {prompt[:40]}", flush=True)

            # Service verification is completed by an operator in the private browser.
            # This deployment never attempts to solve or bypass a challenge.
            for _ in range(20):
                await page.wait_for_timeout(1000)
                frame = find_captcha_frame(page)
                if frame:
                    print(f"[{account}] Verification required: open the private browser screen.", flush=True)
                    deadline = time.monotonic() + config.MANUAL_VERIFY_TIMEOUT
                    while find_captcha_frame(page):
                        if time.monotonic() >= deadline:
                            raise RiskControlError("Manual verification required; operator did not complete it in time")
                        await page.wait_for_timeout(1000)
                    await page.wait_for_timeout(3000)
                    break

            # ---- Wait for real conversation_id ----
            conv_id = ""
            for _ in range(30):
                await page.wait_for_timeout(1000)
                tail = page.url.rstrip("/").split("/")[-1]
                if tail.isdigit():
                    conv_id = tail
                    break
            if not conv_id:
                await page.screenshot(path="no_conv.png")
                raise TimeoutError("conversation_id not acquired within 30s")
            print(f"[{account}] conversation_id={conv_id}, polling for video...", flush=True)

            deadline = time.time() + timeout
            if on_conversation_id:
                on_conversation_id(account, conv_id, deadline)
            return await poll_conversation(account, page, context, conv_id, timeout, on_poll, on_balance)
        finally:
            await context.close()


async def _main():
    account = sys.argv[1] if len(sys.argv) > 1 else "acc1"
    prompt = sys.argv[2] if len(sys.argv) > 2 else "An orange cat napping on a sunny windowsill"
    ratio = sys.argv[3] if len(sys.argv) > 3 else None
    duration = int(sys.argv[4]) if len(sys.argv) > 4 else None
    model = sys.argv[5] if len(sys.argv) > 5 else "seedance_v2.0"
    result = await generate_video(account, prompt, ratio, duration, model=model)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except Exception:
        import traceback
        traceback.print_exc()
