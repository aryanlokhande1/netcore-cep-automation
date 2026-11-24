"""Generate the tested Chromium policy from a verified Docker default profile."""

import argparse
import hashlib
import json
from pathlib import Path


MOBY_DEFAULT_SHA256 = '01536f1d1df938ae611eba20d6349e0de7a99b6ecdee1549427a0b01b8301e28'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('moby_default', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    raw = args.moby_default.read_bytes()
    if hashlib.sha256(raw).hexdigest() != MOBY_DEFAULT_SHA256:
        parser.error('expected the unmodified Moby 28.3.3 default profile')
    profile = json.loads(raw)
    profile['syscalls'].append({'names': ['clone', 'unshare', 'setns'], 'action': 'SCMP_ACT_ALLOW'})
    args.output.write_text(json.dumps(profile) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
