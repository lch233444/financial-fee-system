"""Download pinned upstream assets; never use local business or login files."""
import hashlib
import io
import json
import platform
import tarfile
import urllib.request
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "packaging/macos-assets.json").read_text())


def fetch(url, digest):
    data = urllib.request.urlopen(url, timeout=120).read()
    if hashlib.sha256(data).hexdigest() != digest.lower():
        raise RuntimeError(f"Upstream asset checksum mismatch: {url}")
    return data


def main():
    fonts = ROOT / "fonts"
    fonts.mkdir(exist_ok=True)
    font = MANIFEST["font"]
    data = fetch(font["url"], font["sha256"])
    for weight, filename in [(400, "NotoSansSC-Regular.ttf"), (700, "NotoSansSC-Bold.ttf")]:
        instance = instantiateVariableFont(TTFont(io.BytesIO(data)), {"wght": weight}, inplace=True)
        instance.save(fonts / filename)
    (fonts / "OFL.txt").write_bytes(fetch(font["license_url"], font["license_sha256"]))
    arch = {"arm64": "aarch64", "x86_64": "x86_64"}[platform.machine()]
    target = ROOT / "tools/Codex"
    target.mkdir(parents=True, exist_ok=True)
    lines = ["OpenAI Codex CLI 0.149.1 - official macOS assets"]
    for item in MANIFEST["codex"]:
        if f"-{arch}-apple-darwin" not in item["name"]:
            continue
        data = fetch(item["url"], item["archive_sha256"])
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            members = [m for m in archive.getmembers() if m.isfile()]
            assert len(members) == 1 and members[0].name == item["binary_name"]
            binary = archive.extractfile(members[0]).read()
        assert hashlib.sha256(binary).hexdigest() == item["binary_sha256"]
        name = "codex-code-mode-host" if "code-mode-host" in item["name"] else "codex"
        (target / name).write_bytes(binary)
        (target / name).chmod(0o755)
        lines.append(f"{item['binary_sha256'].upper()}  {name}")
    (target / "VERSION.txt").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
