import os
import select
import subprocess
import sys
from time import monotonic

import pytest

from browser.owned_process import OwnedProcess


pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux pidfd and PTY checks')


def test_owned_process_confirms_exit_and_does_not_signal_sibling():
    sleeper = [sys.executable, '-c', 'import time; time.sleep(15)']
    with subprocess.Popen(sleeper) as browser, subprocess.Popen(sleeper) as sibling:
        tracked = OwnedProcess(browser.pid)
        try:
            assert tracked.status_now() == 'ALIVE'
            assert not tracked.exited()
            assert tracked.terminate()
            browser.wait(timeout=2)
            assert tracked.status_now() == 'TERMINATED'
            assert sibling.poll() is None
        finally:
            tracked.close()
            for process in (browser, sibling):
                if process.poll() is None:
                    process.terminate()


@pytest.mark.parametrize(('key', 'decision'), [(b'y', 'yes'), (b'n', 'no')])
def test_operator_confirmation_reads_enter_in_linux_tty(key, decision):
    code = (
        "import asyncio, sys; from browser.operator_console import OperatorConsole; "
        "assert sys.stdin.isatty(); "
        "print('DECISION=' + asyncio.run(OperatorConsole().confirm_tenant_route("
        "'https://example.invalid/', 2)), flush=True)"
    )
    master, slave = os.openpty()
    try:
        with subprocess.Popen([sys.executable, '-c', code], stdin=slave,
                              stdout=subprocess.PIPE, text=False) as process:
            output = b''
            deadline = monotonic() + 3
            while b'[y/N]' not in output and monotonic() < deadline:
                if select.select([process.stdout], [], [], 0.1)[0]:
                    chunk = os.read(process.stdout.fileno(), 4096)
                    if not chunk:
                        break
                    output += chunk
            assert b'[y/N]' in output
            os.write(master, key + b'\n')
            tail, _ = process.communicate(timeout=4)
            assert process.returncode == 0
            assert b'DECISION=' + decision.encode() in tail
    finally:
        os.close(master)
        os.close(slave)
