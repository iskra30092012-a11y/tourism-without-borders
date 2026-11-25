"""Create a source-only public release; never zip the entire working directory."""

from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "release"
ROOT_FILES = [
    "app.py",
    "gunicorn.conf.py",
    "Procfile",
    "Dockerfile",
    ".dockerignore",
    ".gitignore",
    ".env.example",
    "requirements.txt",
    "requirements.lock",
    "requirements-dev.txt",
    "README.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
]
PATTERNS = [
    "templates/*.html",
    "static/*.css",
    "static/*.js",
    "static/*.jpg",
    "tests/test_app.py",
    "tests/live_server.py",
    "tests/browser_check.py",
    "tests/check_accessibility.py",
    "tests/axe.min.js",
    "scripts/*.py",
    ".github/workflows/*.yml",
    "docs/*.md",
    "docs/*.json",
    "docs/licenses/*.txt",
]


def main():
    paths = {ROOT / name for name in ROOT_FILES}
    for pattern in PATTERNS:
        paths.update(ROOT.glob(pattern))
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"Missing or unsafe source file: {path.name}")
        path.resolve().relative_to(ROOT)
    for name in ROOT_FILES:
        if ROOT / name not in paths:
            raise RuntimeError(f"Missing file: {name}")
    OUTPUT.mkdir(exist_ok=True)
    target = OUTPUT / "tourism-without-borders-github.zip"
    manifest = []
    with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            relative = path.relative_to(ROOT).as_posix()
            if any(
                part in {".env", "instance", "__pycache__", "node_modules"}
                for part in path.relative_to(ROOT).parts
            ):
                raise RuntimeError("Private runtime file rejected")
            data = path.read_bytes()
            archive.writestr("tourism-without-borders/" + relative, data)
            manifest.append(sha256(data).hexdigest() + "  " + relative)
        archive.writestr(
            "tourism-without-borders/MANIFEST.sha256", "\n".join(manifest) + "\n"
        )
    with ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("ZIP integrity check failed")
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("Duplicate archive members")
        for name in names:
            if name.startswith("/") or ".." in Path(name).parts:
                raise RuntimeError("Unsafe archive path")
        for line in manifest:
            digest, name = line.split("  ", 1)
            if (
                sha256(archive.read("tourism-without-borders/" + name)).hexdigest()
                != digest
            ):
                raise RuntimeError("Manifest verification failed")
    digest = sha256(target.read_bytes()).hexdigest()
    target.with_suffix(".zip.sha256").write_text(digest + "  " + target.name + "\n")
    print(
        f"OK: {target.name}; {len(paths)} source files + manifest; CRC and SHA-256 verified."
    )


if __name__ == "__main__":
    main()
