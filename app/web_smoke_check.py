"""Credential-free Chromium and QR rendering smoke check for Railway."""
import asyncio
import logging
import tempfile
from app.cloud_web_reader import CloudWebReader

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("web.smoke")


async def main():
    with tempfile.TemporaryDirectory(prefix="telegram-web-smoke-") as profile_dir:
        reader = CloudWebReader(None, None, profile_dir=profile_dir)
        try:
            await reader.start()
            await asyncio.sleep(8)
            status = await reader.status()
            qr_png = await reader.qr_image()
            meta = await reader._page.evaluate("""() => ({
                title: document.title,
                origin: location.origin,
                canvasClasses: [...document.querySelectorAll('canvas')]
                    .slice(0, 12).map(node => (node.className || '').toString().slice(0, 100)),
                qrClassNames: [...document.querySelectorAll('[class*=qr], [class*=QR]')]
                    .slice(0, 12).map(node => (node.className || '').toString().slice(0, 100)),
                passwordInput: !!document.querySelector('input[type=password]')
            })""")
            # Never print QR contents, session material, or any login tokens.
            log.info("CHROMIUM_OK: origin=%s title=%s state=%s", meta["origin"], meta["title"], status)
            log.info("QR_LAYOUT: canvasClasses=%s qrClassNames=%s passwordPrompt=%s",
                     meta["canvasClasses"], meta["qrClassNames"], meta["passwordInput"])
            log.info("QR_CAPTURE: available=%s imageBytes=%s", bool(qr_png), len(qr_png or b""))
            if meta["origin"] != "https://web.telegram.org":
                raise RuntimeError("Unexpected Telegram Web origin")
            if not qr_png:
                raise RuntimeError("Telegram Web QR cannot be captured by current pilot")
            log.info("SMOKE_SUCCESS: Railway Chromium and anonymous QR rendering verified")
        finally:
            if reader._context:
                await reader._context.close()
            if reader._pw:
                await reader._pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
