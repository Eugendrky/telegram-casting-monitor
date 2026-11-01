"""No-credentials Chromium smoke check for Telegram Web K on Railway."""
import asyncio
import logging
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log=logging.getLogger("web.smoke")

async def main():
    async with async_playwright() as playwright:
        browser=await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            page=await browser.new_page(accept_downloads=False)
            response=await page.goto("https://web.telegram.org/k/",wait_until="domcontentloaded",timeout=45000)
            await page.wait_for_timeout(4000)
            title=await page.title()
            status=response.status if response else None
            log.info("CHROMIUM_OK; TELEGRAM_WEB_HTTP=%s; TITLE=%r; URL=%s",status,title,page.url)
            if not status or status>=400 or "web.telegram.org" not in page.url:
                raise RuntimeError("Telegram Web is not reachable")
            log.info("SMOKE_SUCCESS: browser and Telegram Web load, no credentials supplied")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
