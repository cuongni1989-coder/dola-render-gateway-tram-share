"""Verify the packaged Studio UX with simulated video API responses, without contacting Dola."""
import asyncio
import json
from patchright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            payloads = []
            task = "video_" + "a" * 32

            async def create(route):
                payloads.append(route.request.post_data_json)
                assert route.request.headers["authorization"] == "Bearer test-only-key"
                await route.fulfill(status=200, content_type="application/json", body=json.dumps({"id": task, "status": "queued"}))

            async def result(route):
                await route.fulfill(status=200, content_type="application/json", body=json.dumps({"id": task, "status": "completed", "video_url": "http://127.0.0.1:8000/videos/simulated.mp4"}))

            await page.route("**/v1/videos/generations", create)
            await page.route("**/v1/videos/video_*", result)
            await page.goto("http://127.0.0.1:8000/studio")
            await page.locator("#key").fill("test-only-key")
            await page.locator("#prompt").fill("A red product on a white table")
            await page.locator("#images").fill("https://example.com/product.png")
            await page.locator("#submit").click()
            await page.wait_for_function("document.getElementById('status').textContent === 'Hoàn thành'")
            assert payloads == [{"model": "seedance-2.0", "prompt": "A red product on a white table", "ratio": "9:16", "duration": 10, "reference_images": ["https://example.com/product.png"]}]
            assert await page.locator("#download").is_visible()
            await page.locator("#logout").click()
            assert await page.locator("#key").input_value() == ""
            assert not await page.locator("#download").is_visible()
            assert not errors, errors
            print("Packaged browser Studio smoke test PASS (simulated video responses)")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
