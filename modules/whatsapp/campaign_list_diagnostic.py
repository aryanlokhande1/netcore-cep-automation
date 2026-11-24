"""Passive, sanitized observations for a single owned Campaigns tab."""

import json
import re
from time import monotonic
from urllib.parse import urlsplit

from browser.readonly_cdp import ReadOnlyCDP


def safe_request_path(url, host):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname != host or not parsed.path:
        return None
    segments = '/'.join(
        segment if re.fullmatch(r'[A-Za-z][A-Za-z_.-]{0,63}', segment) else '[redacted]'
        for segment in parsed.path.split('/') if segment
    )
    return '/' + segments + ('/' if segments and parsed.path.endswith('/') else '')


def error_category(event):
    method, params = event['method'], event.get('params', {})
    if method == 'Runtime.exceptionThrown':
        error = params.get('exceptionDetails', {}).get('exception', {})
        kind = error.get('className')
        return kind if kind in {'TypeError', 'ReferenceError', 'SyntaxError', 'RangeError'} else 'UNCAUGHT_EXCEPTION'
    if method == 'Runtime.consoleAPICalled' and params.get('type') in {'error', 'assert'}:
        return 'CONSOLE_ERROR'
    if method == 'Log.entryAdded':
        entry = params.get('entry', {})
        if entry.get('level') != 'error':
            return None
        source = entry.get('source')
        return {'network': 'BROWSER_NETWORK_ERROR', 'security': 'BROWSER_SECURITY_ERROR',
                'javascript': 'BROWSER_JAVASCRIPT_ERROR'}.get(source, 'BROWSER_ERROR')
    return None


class CampaignListDiagnostic:
    def __init__(self, host):
        self.host = host
        self.started = monotonic()
        self.phase = 'LOGIN_AND_LANDING'
        self.requests = []
        self.active = {}
        self.errors = []
        self.rendering = []
        self.milestones = []
        self.dropped = 0
        self.monitor = None
        self.last_document_get_path = None

    def elapsed(self):
        return round((monotonic() - self.started) * 1000)

    async def start(self, browser):
        owner = browser.guard.session_states[browser.guard.primary_session]
        self.monitor = ReadOnlyCDP(self.observe, browser_errors=True)
        browser.discovery = self.monitor
        await self.monitor.start(browser.browser.cdp_url, (owner.target,))
        self.mark('PASSIVE_LISTENER_READY')

    def mark(self, action, **facts):
        self.milestones.append({'ms': self.elapsed(), 'action': action, **facts})

    def render(self, url, state):
        shape = state['shape']
        self.rendering.append({
            'ms': self.elapsed(), 'phase': self.phase, 'path': safe_request_path(url, self.host),
            'document_ready': shape['ready_state'], 'campaigns_heading': shape['campaigns_heading'],
            'loading_indicator': state['busy'], 'table_headers': shape['table_headers'],
            'visible_rows': state['visible_rows'], 'empty_state': state['no_results'],
            'search_trigger_count': state['search_trigger_count'], 'search_input_count': state['search_count'],
            'engage_count': state['engage_count'], 'campaigns_link_count': state['campaigns_link_count'],
        })

    def observe(self, event):
        method, p = event.get('method'), event.get('params', {})
        key = (event.get('sessionId'), p.get('requestId'))
        if method == 'Network.requestWillBeSent':
            previous = self.active.pop(key, None)
            if previous is not None and p.get('redirectResponse'):
                previous.update(status=int(p['redirectResponse']['status']), ended_ms=self.elapsed(),
                                outcome='REDIRECT')
            request = p.get('request', {})
            path = safe_request_path(request.get('url', ''), self.host)
            kind = p.get('type')
            if kind == 'Document':
                self.last_document_get_path = path if request.get('method') == 'GET' else None
            if path and kind in {'Document', 'XHR', 'Fetch', 'Script', 'Stylesheet'}:
                if len(self.requests) >= 300:
                    self.dropped += 1
                    return
                row = {'phase': self.phase, 'path': path, 'kind': kind,
                       'started_ms': self.elapsed(), 'outcome': 'PENDING'}
                self.requests.append(row)
                self.active[key] = row
        elif method == 'Network.responseReceived' and key in self.active:
            self.active[key].update(status=int(p['response']['status']), response_ms=self.elapsed())
        elif method in {'Network.loadingFinished', 'Network.loadingFailed'} and key in self.active:
            row = self.active.pop(key)
            row.update(ended_ms=self.elapsed(), outcome='COMPLETED')
            if method == 'Network.loadingFailed':
                reason = p.get('errorText', '')
                blocked = p.get('blockedReason')
                row.update(outcome='FAILED',
                           reason=reason if re.fullmatch(r'net::ERR_[A-Z0-9_]{1,55}', reason)
                           else 'BLOCKED' if blocked else 'CANCELLED' if p.get('canceled') else 'OTHER')
        else:
            category = error_category(event)
            if category is not None and len(self.errors) < 80:
                self.errors.append({'ms': self.elapsed(), 'phase': self.phase, 'category': category})

    def save(self, path, *, frames=None):
        path.write_text(json.dumps({
            'milestones': self.milestones, 'rendering': self.rendering, 'requests': self.requests,
            'browser_errors': self.errors, 'dropped_requests': self.dropped,
            'listener_failure': self.monitor.failure if self.monitor is not None else 'NOT_STARTED',
            **({'frames': frames} if frames is not None else {}),
        }, indent=2) + '\n', encoding='utf-8')
