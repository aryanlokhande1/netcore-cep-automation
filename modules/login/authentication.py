"""Authentication input boundary: no credential-bearing payloads enter graph state."""
class VisibleBrowserHandoff:
    async def complete(self, browser):
        import asyncio
        import sys
        if not sys.stdin.isatty():
            raise ValueError('Human authentication requires an interactive terminal')
        print('Complete login or verification directly in Chromium. Then type completed or cancel here (input hidden):', flush=True)
        if sys.platform == 'win32':
            import msvcrt
            signal = ''
            while True:
                if msvcrt.kbhit():
                    char = msvcrt.getwch()
                    if char == '\x03':
                        raise KeyboardInterrupt
                    if char in ('\r', '\n'):
                        return 'completed' if signal == 'completed' else 'cancel'
                    if char == '\b':
                        signal = signal[:-1]
                    elif char.isprintable():
                        signal = (signal+char)[-32:]
                await asyncio.sleep(.05)
        else:
            import select
            while not select.select([sys.stdin], [], [], 0)[0]:
                await asyncio.sleep(.05)
            return 'completed' if sys.stdin.readline().strip() == 'completed' else 'cancel'

class SyntheticHandoff:
    """Test-only: synthetic fixture values go directly to Stagehand locators."""
    async def complete(self, browser):
        if not browser.fixture:
            raise PermissionError('Synthetic input is forbidden for live sites')
        from app.safety import require_fixture_browser
        require_fixture_browser(browser)
        o = await browser.observe()
        if o.challenge:
            browser.budget()
            await browser.page.locator('#complete').send_click_event()
            await browser.settle('#dashboard')
        else:
            browser.claim_login_submission()
            for selector, value in (('#account','synthetic'),('#username','tester'),('#password','fixture-only')):
                browser.budget()
                await browser.page.locator(selector).fill(value)
            browser.budget()
            await browser.page.locator('#submit').send_click_event()
            await browser.settle('#dashboard,#challenge,#rejected')
        return 'completed'
