"""Run with the wheel environment's Python; prove imports and defaults work off-tree."""
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import tempfile
import socket
import time
import urllib.error
import urllib.request


def check_server(folder, env):
    from log_analyzer.launcher import server_command
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    with (Path(folder)/'server.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen(server_command(port), cwd=folder, env=env, stdout=log,
                                   stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            deadline = time.monotonic()+30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('Dashboard server exited before becoming healthy')
                try:
                    # A loopback health endpoint confirms startup, not application correctness.
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=1) as response:
                        if response.status == 200 and response.read().strip() == b'ok':
                            return
                except (urllib.error.URLError, TimeoutError, ConnectionError):
                    pass
                time.sleep(0.2)
            raise TimeoutError('Dashboard server did not become healthy within 30 seconds')
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def main():
    import main as application
    from log_analyzer.runtime import sample_directory, source_directory
    if source_directory() is not None:
        raise RuntimeError('This check requires an installed wheel, not an editable/source import')
    scripts = Path(sysconfig.get_path('scripts'))
    command = scripts/('log-analyzer.exe' if os.name == 'nt' else 'log-analyzer')
    launcher = scripts/('log-analyzer-dashboard.exe' if os.name == 'nt' else 'log-analyzer-dashboard')
    assert command.is_file() and launcher.is_file()
    assert all(path.is_file() for path in application.DEFAULT_LOG_FILES)
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    env.pop('PYTHONPATH', None)
    with tempfile.TemporaryDirectory(prefix='wheel-smoke-') as folder:
        result = subprocess.run([str(command), '--export', 'json'], cwd=folder, env=env,
                                capture_output=True, text=True, encoding='utf-8', timeout=120)
        if result.returncode:
            raise AssertionError(result.stdout+'\n'+result.stderr)
        document = json.loads((Path(folder)/'reports/investigation.json').read_text(encoding='utf-8'))
        assert document['selected']['event_count'] == 10048
        assert document['selected']['error_count'] == 543
        assert document['selected']['warning_count'] == 1487
        assert (Path(folder)/'logs.db').is_file()
        help_result = subprocess.run([str(launcher), '--help'], cwd=folder, env=env,
                                     capture_output=True, text=True, timeout=30)
        assert help_result.returncode == 0
        # AppTest executes the actual installed dashboard rather than the checkout.
        from streamlit.testing.v1 import AppTest
        previous = Path.cwd()
        try:
            os.chdir(folder)
            app = AppTest.from_file(str(Path(application.__file__).with_name('dashboard.py'))).run(timeout=30)
            assert not app.exception, str(app.exception)
            assert app.metric[1].value == '48'
        finally:
            os.chdir(previous)
        check_server(folder, env)
    print(json.dumps({'installed_module': str(Path(application.__file__).resolve()),
                      'samples': str(sample_directory()), 'cli_events': 10048,
                      'dashboard_events': 48, 'loopback_server_health': 'passed', 'status': 'passed'}, indent=2))


if __name__ == '__main__':
    main()
