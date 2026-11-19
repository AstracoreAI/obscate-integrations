"""The HTTP client and fake API are vendored into each package; copies must not drift."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_vendored_copies_in_sync():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "sync_client.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_no_secrets_tracked():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    assert not [f for f in files if Path(f).name == ".env" or Path(f).name.startswith(".env.") and
                not f.endswith(".env.example")]
    for f in files:
        p = ROOT / f
        if p.suffix in {".py", ".md", ".json", ".toml"}:
            text = p.read_text(errors="ignore")
            assert not re.search(r"ob_[a-z0-9]{8}_[A-Za-z0-9_\-]{43}", text), f"Obscate key in {f}"
            assert ("sk-ant-" + "api") not in text, f"Anthropic key in {f}"
