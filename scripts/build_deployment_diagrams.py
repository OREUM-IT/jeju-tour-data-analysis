"""Render reviewed Graphviz design files; does not deploy any service."""
from pathlib import Path
import subprocess
import shutil

ROOT = Path(__file__).resolve().parents[1]


def main():
    executable = shutil.which("dot")
    if not executable:
        raise SystemExit("Graphviz dot is required")
    target = ROOT / "reports/assets"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("ollama-mcp-vercel-architecture", "ollama-mcp-job-sequence", "multi-provider-tunnels-database", "oreumit-next-deployment", "oreumit-next-steps"):
        source = ROOT / "design" / (name + ".dot")
        for kind in ("png", "svg"):
            output = target / (name + "." + kind)
            subprocess.run([executable, "-T" + kind, "-Gdpi=170", str(source), "-o", str(output)], check=True)
        print(target / (name + ".png"))


if __name__ == "__main__":
    main()
