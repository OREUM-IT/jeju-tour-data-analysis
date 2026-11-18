"""Package only reviewed example files; never include host credentials."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples" / "ollama-tunnel"
FILES = ("README.md", ".gitignore", "gateway.mjs", "gateway.test.mjs", "configure.mjs", "check.mjs")

if __name__ == "__main__":
    target = ROOT / "reports" / "ollama-tunnel-starter.zip"
    with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(SOURCE / name, "ollama-tunnel/" + name)
    print(target)
