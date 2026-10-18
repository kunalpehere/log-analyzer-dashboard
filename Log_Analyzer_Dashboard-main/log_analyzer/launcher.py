"""Launch the installed dashboard with explicit local resource settings."""
import argparse
from pathlib import Path
import subprocess
import sys


def server_command(port):
    import dashboard
    return [
        sys.executable, '-m', 'streamlit', 'run', str(Path(dashboard.__file__).resolve()),
        '--server.address=127.0.0.1', f'--server.port={port}',
        '--server.maxUploadSize=50', '--server.headless=true',
        '--browser.gatherUsageStats=false',
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(description='Launch the local Log Analyzer dashboard')
    parser.add_argument('--port', type=int, default=8501)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    return subprocess.call(server_command(args.port))

