"""Reduce a passive JSON response to bounded, sanitized Login signal metadata."""
from collections import deque
from hashlib import sha256
from urllib.parse import unquote_plus, urlsplit

AUTH_FIELDS = {
    'authenticated', 'isAuthenticated', 'is_authenticated', 'loggedIn',
    'logged_in', 'isLoggedIn', 'is_logged_in', 'isauthenticated', 'isloggedin', 'loggedin',
}
TENANT_FIELDS = {
    'account', 'accountId', 'account_id', 'accountName', 'account_name', 'acctId',
    'tenant', 'tenantId', 'tenant_id', 'tenantName', 'tenant_name', 'tenantSlug',
    'company', 'companyId', 'companyName', 'organization', 'organizationId',
    'organizationName', 'organisation', 'organisationId', 'orgId',
}
CONTAINERS = {
    'account', 'auth', 'authentication', 'context', 'currentAccount', 'currentTenant',
    'currentUser', 'data', 'identity', 'organization', 'organisation', 'profile',
    'response', 'result', 'session', 'tenant', 'user',
}
PATH_SEGMENTS = CONTAINERS | {
    'api', 'admin', 'ajax', 'app', 'dashboard', 'index.php', 'info', 'me', 'read',
    'getAccount', 'getProfile', 'getSession', 'getTenant', 'v1', 'v2', 'v3',
}
QUERY_NAMES = TENANT_FIELDS | {'action', 'method', 'mode', 'type', 'version', 'locale'}
_LIMIT = 64


def safe_request_locator(path):
    url = urlsplit(path)
    segments = [segment for segment in url.path.split('/') if segment]
    names = [unquote_plus(part.partition('=')[0]) for part in url.query.split('&') if part]
    return {
        'path_template': '/' + '/'.join(segment if segment in PATH_SEGMENTS else ':redacted'
                                       for segment in segments[:16]),
        'path_segments_truncated': len(segments) > 16,
        'path_sha256': sha256(url.path.encode('utf-8')).hexdigest(),
        'query_names': sorted(set(names) & QUERY_NAMES),
        'other_query_parameters': sum(name not in QUERY_NAMES for name in names),
    }


def inspect_identity_branches(data, expected):
    """Inspect named objects next to the root and leave arbitrary subtrees unread."""
    queue = deque([('', data, 0)])
    compared_auth = []
    compared_tenant = []
    pairs = []
    limits = set()
    denied = False
    checked = 0
    arrays_skipped = 0
    while queue and checked < _LIMIT:
        prefix, item, depth = queue.popleft()
        if not isinstance(item, dict):
            arrays_skipped += isinstance(item, list)
            continue
        checked += 1
        auth_hits, tenant_hits = [], []
        for key in sorted(AUTH_FIELDS & item.keys()):
            value = item[key]
            is_true = value is True
            denied |= value is False or (type(value) is str and value.lower() in
                                         {'false', 'inactive', 'unauthenticated', 'logged_out', 'denied', 'expired'})
            field = {'path': f'{prefix}/{key}', 'type': json_type(value), 'is_true': is_true}
            compared_auth.append(field)
            if is_true:
                auth_hits.append(field)
        for key in sorted(TENANT_FIELDS & item.keys()):
            value = item[key]
            if isinstance(value, (dict, list)):
                continue
            exact = isinstance(value, str) and bool(expected) and value == expected
            field = {'path': f'{prefix}/{key}', 'type': json_type(value), 'exact_expected_account': exact}
            compared_tenant.append(field)
            if exact:
                tenant_hits.append(field)
        for auth in auth_hits:
            for tenant in tenant_hits:
                pairs.append({'authenticated_path': auth['path'], 'authenticated_type': auth['type'],
                              'tenant_path': tenant['path'], 'tenant_type': tenant['type'],
                              'authenticated_true': True, 'tenant_exact': True})
        for key in sorted(CONTAINERS & item.keys()):
            value = item[key]
            if isinstance(value, list):
                arrays_skipped += 1
            elif isinstance(value, dict):
                if depth < 5:
                    queue.append((f'{prefix}/{key}', value, depth + 1))
                else:
                    limits.add('IDENTITY_BRANCH_DEPTH_LIMIT')
    if queue:
        limits.add('IDENTITY_OBJECT_LIMIT')
    if denied:
        status = 'EXPLICIT_AUTHENTICATION_DENIAL'
    elif limits:
        status = 'IDENTITY_BRANCH_SCAN_LIMIT'
    elif len(pairs) == 1:
        status = 'EXPLICIT_AUTH_AND_EXACT_TENANT_PAIR_OBSERVED'
    elif pairs:
        status = 'AMBIGUOUS_AUTH_TENANT_PAIRS'
    elif not isinstance(data, dict):
        status = 'TOP_LEVEL_IDENTITY_OBJECT_UNAVAILABLE'
    elif not any(field['is_true'] for field in compared_auth):
        status = 'EXPLICIT_AUTHENTICATED_TRUE_NOT_FOUND_IN_NAMED_OBJECTS'
    elif not any(field['exact_expected_account'] for field in compared_tenant):
        status = 'EXACT_TENANT_NOT_FOUND_IN_NAMED_OBJECTS'
    else:
        status = 'AUTHENTICATION_AND_TENANT_IN_DIFFERENT_OBJECTS'
    return {'status': status, 'pairs': pairs[:8], 'authenticated_fields': compared_auth[:32],
            'tenant_fields': compared_tenant[:32], 'limits': sorted(limits),
            'identity_objects_checked': checked, 'named_arrays_skipped': arrays_skipped}


def json_type(value):
    return {bool: 'boolean', str: 'string', int: 'number', float: 'number', type(None): 'null',
            dict: 'object', list: 'array'}.get(type(value), 'unknown')
