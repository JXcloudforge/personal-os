from pathlib import Path
import shutil
import subprocess
import pytest


def test_manager_lifecycle_with_mocked_windows_operations():
    shell = shutil.which("powershell.exe")
    if not shell:
        pytest.skip("Windows PowerShell required")
    script = Path(__file__).resolve().parents[2] / "scripts/tests/manager-lifecycle.tests.ps1"
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(script)],
                            capture_output=True, text=True, errors="replace", timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout
