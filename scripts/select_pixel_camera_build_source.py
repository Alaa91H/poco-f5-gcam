"""Keep version-specific compatibility builds separate from latest-release audits."""

import argparse
import json
from pathlib import Path

SUPPORTED_VERSION = '11.0.073.972752740.32'


def select_release(latest: dict, supported: dict, standalone: bool) -> dict:
    if standalone:
        if supported.get('selected', {}).get('version') != SUPPORTED_VERSION:
            raise ValueError('Compatibility patch source version is unsupported')
        return supported
    return latest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--standalone', choices=('true', 'false'), required=True)
    parser.add_argument('--latest-lock', type=Path, default=Path('device/marble/upstream/pixel-camera.json'))
    parser.add_argument('--compatibility-lock', type=Path, default=Path('device/marble/compatibility-source.json'))
    args = parser.parse_args()
    latest = json.loads(args.latest_lock.read_text(encoding='utf-8'))
    supported = json.loads(args.compatibility_lock.read_text(encoding='utf-8'))
    standalone = args.standalone == 'true'
    selected = select_release(latest, supported, standalone)
    print(f'lock={args.compatibility_lock if standalone else args.latest_lock}')
    print(f'version={selected["selected"]["version"]}')


if __name__ == '__main__':
    main()
