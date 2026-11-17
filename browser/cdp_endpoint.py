"""Validate local browser-wide CDP ownership without serializing debugger URLs."""
from urllib.parse import urlsplit

def browser_websocket(cdp_url,version):
    base=urlsplit(cdp_url);endpoint=version.get('webSocketDebuggerUrl','');ws=urlsplit(endpoint)
    loopback={'127.0.0.1','localhost','::1'}
    if (base.hostname not in loopback or ws.hostname not in loopback or ws.scheme!='ws'
        or ws.port!=base.port or ws.username or ws.password or ws.query or ws.fragment
        or not ws.path.startswith('/devtools/browser/')):
        raise PermissionError('Expected owned browser-level loopback CDP endpoint')
    return endpoint
