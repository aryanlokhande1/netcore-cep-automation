"""Exercise the normal headed browser lifecycle with a loopback page."""

import asyncio
import json
import os
from pathlib import Path

from app.config import Config, OriginPolicy
from app.trace import Trace
from browser.stagehand_client import StagehandClient
from evals.fixture import fixture_server


async def main():
    if os.getuid() == 0 or os.environ.get('CI'):
        raise RuntimeError('Use a non-root Linux browser with CI unset')
    trace = Trace()
    config = Config(executable=os.environ['CHROMIUM_EXECUTABLE_PATH'], headless=False)
    with fixture_server() as server:
        browser = StagehandClient(config, OriginPolicy({server.base}), trace, fixture=True)
        try:
            await browser.start()
            assert browser.process.status_now() == 'ALIVE'
            flags = Path(f'/proc/{browser.process.pid}/cmdline').read_bytes().split(b'\0')
            assert b'--no-sandbox' not in flags and b'--ignore-certificate-errors' not in flags
            await browser.navigate(server.base + '/start?scenario=valid')
            await browser.guard.ensure_observable()
            assert await browser.page.evaluate("Boolean(document.querySelector('input[type=password]'))")
        finally:
            await browser.close()
        assert trace.cleanup_result == 'CLOSED_AND_PROFILE_REMOVED'
        assert trace.cleanup_diagnostic['process_exit_confirmed'] is True
        assert not browser.profile.exists()
    print(json.dumps({'run_id': trace.run_id, 'headed': True, 'loopback_page_verified': True,
                      'cleanup': trace.cleanup_result, 'profile_removed': True}), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
