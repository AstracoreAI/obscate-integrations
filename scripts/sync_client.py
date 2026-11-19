"""Copy shared/_client.py and shared/fake_api.py into every package.

    python scripts/sync_client.py          # copy
    python scripts/sync_client.py --check  # exit 1 if any copy has drifted
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = {
    "_client.py": [
        "obscate-mcp/src/obscate_mcp/_client.py",
        "langchain-obscate/src/langchain_obscate/_client.py",
        "llama-index-postprocessor-obscate/src/llama_index/postprocessor/obscate/_client.py",
    ],
    "fake_api.py": [
        "obscate-mcp/tests/fake_api.py",
        "langchain-obscate/tests/fake_api.py",
        "llama-index-postprocessor-obscate/tests/fake_api.py",
    ],
}


def main() -> int:
    check = "--check" in sys.argv
    drift = []
    for name, dests in TARGETS.items():
        src = ROOT / "shared" / name
        for d in dests:
            dst = ROOT / d
            if check:
                if not dst.exists() or dst.read_bytes() != src.read_bytes():
                    drift.append(d)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
    if drift:
        print("out of sync (run python scripts/sync_client.py):", *drift, sep="\n  ")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
