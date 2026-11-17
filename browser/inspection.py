"""Optional operator inspection, bounded and always followed by profile cleanup."""
import asyncio
import sys

async def inspection_hold(seconds):
    if not 1<=seconds<=300 or not sys.stdin.isatty():
        raise ValueError('Inspection hold requires an interactive terminal and 1-300 seconds')
    print('Browser inspection hold. Press Enter to close the owned browser; automatic cleanup follows the hold deadline.',flush=True)
    if sys.platform=='win32':
        import msvcrt
        async with asyncio.timeout(seconds):
            while True:
                if msvcrt.kbhit() and msvcrt.getwch() in ('\r','\n'):
                    return
                await asyncio.sleep(.05)
    else:
        import select
        async with asyncio.timeout(seconds):
            while not select.select([sys.stdin],[],[],0)[0]:
                await asyncio.sleep(.05)
            sys.stdin.readline()
