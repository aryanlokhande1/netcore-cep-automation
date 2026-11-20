"""Bounded enum-only terminal choices for supervised browser diagnostics."""
import asyncio
from collections.abc import Mapping
from datetime import datetime, timezone
import os
import re
import select
import sys
from time import monotonic, sleep as _sleep
from uuid import uuid4


_PROMPT_ID = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")
_CHOICE_ID = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")
_MAXIMUM_WAIT_SECONDS = 600


class OperatorConsole:
    def confirm_readonly(self, timeout_seconds=300):
        if not self._custom_reader and not sys.stdin.isatty():
            raise RuntimeError('INTERACTIVE_TERMINAL_REQUIRED')
        self._write('Login completed. Type c to continue or x to abort:\n')
        started=self.clock();buffer=''
        self._record('whatsapp_cx_checkpoint','prompt_open',started,0,0)
        while self.clock()-started<timeout_seconds:
            key=self.read_key()
            if key is None:self.sleep(.05);continue
            if key in ('\r','\n'):
                if buffer in ('c','x'):
                    self._record('whatsapp_cx_checkpoint','input_valid',started,0,0,
                        choice=buffer,status='SELECTED',valid=True)
                    return buffer=='c'
                buffer='';self._write('Enter c or x, then press Enter.\n')
            elif key in ('\b','\x7f'):buffer=buffer[:-1]
            else:buffer=(buffer+key.lower())[-2:]
        self._record('whatsapp_cx_checkpoint','timeout',started,0,0,choice='x',status='TIMED_OUT_DEFAULT')
        return False

    def confirm_setup_write(self, timeout_seconds):
        for answer in self._setup_write_answers(timeout_seconds):
            if answer is not None:
                return answer == 'yes'
            self.sleep(.05)

    async def confirm_operator_login(self, timeout_seconds):
        return await self._confirm_async(timeout_seconds, prompt_id='whatsapp_setup_write',
                                         prompt='Write the approved WhatsApp Setup fields? [y/N] ',
                                         receipt='Approval received')

    async def confirm_tenant_route(self, observed_url, timeout_seconds):
        return await self._confirm_async(timeout_seconds, prompt_id='whatsapp_tenant_confirmation',
            prompt=f'Observed Netcore URL {observed_url}\nDoes this match your signed-in Netcore tab? [y/N] ',
            receipt='Tenant confirmation received')

    async def confirm_page1_setup(self, observed_url, timeout_seconds):
        return await self._confirm_async(timeout_seconds, prompt_id='whatsapp_page1_setup',
            prompt=(f'Observed Netcore URL {observed_url}\n'
                    'Confirm this dashboard tenant and allow filling the four approved WhatsApp Setup fields? [y/N] '),
            receipt='Setup field approval received')

    async def confirm_page1_next_step(self, timeout_seconds):
        return await self._confirm_async(timeout_seconds, prompt_id='whatsapp_page1_next_step',
            prompt='Click Next Step to submit WhatsApp Setup and open Audience? [y/N] ',
            receipt='Setup transition approval received')

    async def confirm_campaign_next_step(self, stage, destination, timeout_seconds):
        if (stage, destination) not in {('audience', 'content'), ('content', 'schedule')}:
            raise ValueError('UNSUPPORTED_CAMPAIGN_TRANSITION')
        return await self._confirm_async(timeout_seconds, prompt_id='whatsapp_' + stage + '_next_step',
            prompt=f'Click Next Step to submit WhatsApp {stage.title()} and open {destination.title()}? [y/N] ',
            receipt=f'{stage.title()} transition approval received')

    async def _confirm_async(self, timeout_seconds, *, prompt_id, prompt, receipt):
        if not self._custom_reader and os.name == 'nt':
            self._discard_queued_windows_keys()
        for answer in self._setup_write_answers(timeout_seconds, prompt_id=prompt_id,
                                                prompt=prompt, receipt=receipt):
            if answer is not None:
                return answer
            await asyncio.sleep(.05)

    def _discard_queued_windows_keys(self):
        if not sys.stdin.isatty():
            raise RuntimeError('INTERACTIVE_TERMINAL_REQUIRED')
        for _ in range(256):
            if self.read_key() is None:
                return
        raise RuntimeError('SETUP_CONSOLE_INPUT_BUFFER_NOT_CLEAR')

    def _setup_write_answers(self, timeout_seconds, *, prompt_id='whatsapp_setup_write',
                             prompt='Write the approved WhatsApp Setup fields? [y/N] ', receipt=None):
        if not self._custom_reader and not sys.stdin.isatty():
            raise RuntimeError('INTERACTIVE_TERMINAL_REQUIRED')
        started = self.clock()
        answer = ''
        extended_key = False
        self._write(prompt)
        self._record(prompt_id, 'prompt_open', started, 0, 0, timeout_seconds=timeout_seconds)
        while self.clock() - started < timeout_seconds:
            key = self.read_key()
            if key is None:
                yield None
                continue
            if extended_key:
                extended_key = False
                continue
            if key in ('\x00', '\xe0'):
                extended_key = True
                continue
            if key in ('\r', '\n'):
                normalized = answer.strip().lower()
                self._write('\n')
                if receipt and normalized not in ('', 'y', 'n'):
                    self._record(prompt_id, 'input_invalid', started, 1, 0,
                                 valid=False, reason='EXPECTED_Y_OR_N')
                    self._write('Type y or n, then press Enter. ')
                    answer = ''
                    continue
                choice = 'yes' if normalized == 'y' else 'no'
                if choice == 'yes' and receipt:
                    self._write(receipt + '\n')
                self._record(prompt_id, 'input_valid', started, 0, 0, choice=choice, status='SELECTED')
                yield choice
                return
            if key in ('\b', '\x7f'):
                answer = answer[:-1]
            elif len(answer) < 64:
                answer += key
        self._write('\n')
        self._record(prompt_id, 'timeout', started, 0, 0, status='TIMED_OUT')
        yield 'timeout'

    def __init__(self, *, read_key=None, clock=monotonic, sleep=_sleep, output=None, event_sink=None):
        self._custom_reader = read_key is not None
        self.read_key = read_key or self._read_terminal_key
        self.clock = clock
        self.sleep = sleep
        self.output = output or sys.stdout
        self.event_sink = event_sink
        self.events = []

    @staticmethod
    def _read_terminal_key():
        if os.name == "nt":
            import msvcrt
            return msvcrt.getwch() if msvcrt.kbhit() else None
        return sys.stdin.read(1) if select.select([sys.stdin], [], [], 0)[0] else None

    def _write(self, text):
        self.output.write(text)
        self.output.flush()

    def _record(
        self,
        prompt_id,
        event,
        started,
        invalid_attempts,
        timeout_extensions,
        *,
        choice=None,
        status=None,
        input_kind=None,
        valid=None,
        reason=None,
        timeout_seconds=None,
        maximum_seconds=None,
    ):
        event = {
            "event_id": uuid4().hex,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "prompt_id": prompt_id,
            "event": event,
            "choice": choice,
            "status": status,
            "elapsed_seconds": round(self.clock() - started, 3),
            "invalid_attempts": invalid_attempts,
            "timeout_extensions": timeout_extensions,
        }
        if input_kind is not None:
            event["input_kind"] = input_kind
        if valid is not None:
            event["valid"] = valid
        if reason is not None:
            event["reason"] = reason
        if timeout_seconds is not None:
            event["timeout_seconds"] = timeout_seconds
        if maximum_seconds is not None:
            event["maximum_seconds"] = maximum_seconds
        self.events.append(event)
        if self.event_sink is not None:
            self.event_sink(event)
        return event

    def choose(self, prompt_id, choices, timeout_seconds, maximum_seconds, *, default=None):
        if not isinstance(prompt_id, str) or not _PROMPT_ID.fullmatch(prompt_id):
            raise ValueError("Invalid operator prompt identifier")
        if not isinstance(choices, Mapping) or not 1 <= len(choices) <= 9:
            raise ValueError("Operator choices must contain one to nine enumerated options")
        if any(not isinstance(key, str) or not _CHOICE_ID.fullmatch(key) for key in choices):
            raise ValueError("Operator choice identifiers must be enums")
        if any(not isinstance(label, str) or not label.strip() or len(label) > 160 for label in choices.values()):
            raise ValueError("Operator choice descriptions must be bounded text")
        if type(timeout_seconds) is not int or type(maximum_seconds) is not int:
            raise ValueError("Operator timeouts must be integer seconds")
        if not 1 <= timeout_seconds <= maximum_seconds <= _MAXIMUM_WAIT_SECONDS:
            raise ValueError("Operator timeout is outside the permitted range")
        if default is not None and default not in choices:
            raise ValueError("Operator timeout default must be an enumerated choice")
        if not self._custom_reader and not sys.stdin.isatty():
            raise RuntimeError("INTERACTIVE_TERMINAL_REQUIRED")

        options = list(choices.items())
        started = self.clock()
        self._write(f"\n[{prompt_id}] Choose a numbered option:\n")
        for number, (_, label) in enumerate(options, 1):
            self._write(f"  {number}. {label}\n")
        default_label = choices[default] if default is not None else "No automatic choice"
        self._write(
            f"Timeout: {timeout_seconds}s; active input extends it up to {maximum_seconds}s. "
            f"On timeout: {default_label}.\n"
        )
        self._write("TYPE YOUR SELECTION HERE AND PRESS ENTER.\n")
        self._record(
            prompt_id,
            "prompt_open",
            started,
            0,
            0,
            timeout_seconds=timeout_seconds,
            maximum_seconds=maximum_seconds,
        )

        hard_deadline = started + maximum_seconds
        deadline = started + timeout_seconds
        last_displayed = None
        buffer = ""
        invalid_attempts = 0
        timeout_extensions = 0
        while True:
            now = self.clock()
            remaining = max(0, deadline - now)
            display = int(remaining + 0.999)
            if display != last_displayed:
                self._write(f"\rTime remaining: {display:>3}s  Enter choice: ")
                last_displayed = display
            key = self.read_key()
            if key is not None:
                if not isinstance(key, str) or len(key) != 1:
                    raise ValueError("Terminal reader must return one character or None")
                now = self.clock()
                deadline = min(hard_deadline, max(deadline, now) + timeout_seconds)
                timeout_extensions += 1
                input_kind = (
                    "ENTER" if key in ("\r", "\n") else
                    "BACKSPACE" if key in ("\b", "\x7f") else
                    "DIGIT" if key.isascii() and key.isdigit() else
                    "OTHER"
                )
                self._record(
                    prompt_id,
                    "input_received",
                    started,
                    invalid_attempts,
                    timeout_extensions,
                    input_kind=input_kind,
                )
                if key in ("\r", "\n"):
                    if len(buffer) == 1 and buffer.isdigit():
                        selected = int(buffer)
                        if 1 <= selected <= len(options):
                            choice = options[selected - 1][0]
                            self._write("\n")
                            self._record(
                                prompt_id,
                                "input_valid",
                                started,
                                invalid_attempts,
                                timeout_extensions,
                                choice=choice,
                                status="SELECTED",
                                valid=True,
                            )
                            return choice
                    invalid_attempts += 1
                    reason = "EMPTY" if not buffer else "OUT_OF_RANGE_OR_MULTIPLE_DIGITS"
                    buffer = ""
                    self._write("\nUse one of the numbered options shown above.\n")
                    self._record(
                        prompt_id,
                        "input_invalid",
                        started,
                        invalid_attempts,
                        timeout_extensions,
                        valid=False,
                        reason=reason,
                    )
                elif key in ("\b", "\x7f"):
                    buffer = buffer[:-1]
                elif key.isascii() and key.isdigit() and not buffer:
                    buffer = key
                else:
                    invalid_attempts += 1
                    buffer = ""
                    self._write("\nUse one of the numbered options shown above.\n")
                    self._record(
                        prompt_id,
                        "input_invalid",
                        started,
                        invalid_attempts,
                        timeout_extensions,
                        input_kind=input_kind,
                        valid=False,
                        reason="UNSUPPORTED_INPUT",
                    )
            elif now >= deadline:
                status = "TIMED_OUT_DEFAULT" if default is not None else "TIMED_OUT"
                self._write("\n")
                self._record(
                    prompt_id,
                    "timeout",
                    started,
                    invalid_attempts,
                    timeout_extensions,
                    choice=default,
                    status=status,
                    valid=None,
                )
                if default is not None:
                    return default
                raise TimeoutError("Operator decision deadline expired")
            self.sleep(min(0.1, max(0.0, deadline - self.clock())))
