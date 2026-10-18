"""Record exactly installed third-party versions without inheriting host packages."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import platform
import sys


def inventory():
    return {
        'python': platform.python_version(), 'platform': platform.platform(),
        'system_site_packages_enabled': sys.prefix == sys.base_prefix or any(
            line.strip().lower() == 'include-system-site-packages = true'
            for line in (Path(sys.prefix)/'pyvenv.cfg').read_text(encoding='utf-8').splitlines()),
        'packages': dict(sorted((dist.metadata['Name'], dist.version) for dist in importlib.metadata.distributions())),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    document = inventory()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
