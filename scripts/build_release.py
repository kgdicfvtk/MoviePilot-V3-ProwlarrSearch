from __future__ import annotations

import argparse
import stat
import zipfile
from pathlib import Path, PurePosixPath


RUNTIME_FILES = (
    "__init__.py",
    "client.py",
    "config.py",
    "mapper.py",
    "relay.py",
)
EXPECTED_MEMBERS = tuple(f"prowlarrsearch/{name}" for name in RUNTIME_FILES)
FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
FIXED_MODE = stat.S_IFREG | 0o644
MAX_MEMBER_BYTES = 2 * 1024 * 1024
FORBIDDEN_MARKERS = (
    b"TEST_" + b"PROWLARR_KEY_" + b"DO_NOT_PACKAGE",
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
)


def _validate_member_name(name: str) -> None:
    if "\\" in name or ":" in name:
        raise ValueError("release archive contains an unsafe member name")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError("release archive contains an unsafe member name")


def validate_release_zip(zip_path: Path) -> tuple[str, ...]:
    archive_path = Path(zip_path)
    if not archive_path.is_file() or not zipfile.is_zipfile(archive_path):
        raise ValueError("release archive is not a readable ZIP")

    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            if archive.comment:
                raise ValueError("release archive comment is not allowed")
            infos = archive.infolist()
            names = tuple(info.filename for info in infos)
            if len(names) != len(set(names)):
                raise ValueError("release archive contains duplicate members")
            if names != EXPECTED_MEMBERS:
                raise ValueError("release archive member allowlist mismatch")

            for info in infos:
                _validate_member_name(info.filename)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                    raise ValueError("release archive contains a non-regular member")
                if mode != FIXED_MODE:
                    raise ValueError("release archive permissions are not normalized")
                if info.create_system != 3 or info.date_time != FIXED_TIMESTAMP:
                    raise ValueError("release archive metadata is not normalized")
                if info.compress_type != zipfile.ZIP_DEFLATED:
                    raise ValueError("release archive compression is not normalized")
                if info.flag_bits & 0x1 or info.extra or info.comment:
                    raise ValueError("release archive contains unsupported ZIP metadata")
                if info.file_size > MAX_MEMBER_BYTES:
                    raise ValueError("release archive member is too large")
                content = archive.read(info)
                if any(marker in content for marker in FORBIDDEN_MARKERS):
                    raise ValueError("release archive contains a forbidden marker")
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise ValueError("release archive cannot be validated") from error

    return names


def build_release(source_root: Path, output_zip: Path) -> Path:
    root = Path(source_root).resolve()
    runtime_root = root / "plugins.v3" / "prowlarrsearch"
    output = Path(output_zip).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f"{output.name}.tmp")

    sources: list[tuple[str, bytes]] = []
    for filename, member_name in zip(RUNTIME_FILES, EXPECTED_MEMBERS, strict=True):
        source = runtime_root / filename
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"missing regular runtime file: {filename}")
        content = source.read_bytes()
        if len(content) > MAX_MEMBER_BYTES:
            raise ValueError(f"runtime file is too large: {filename}")
        if any(marker in content for marker in FORBIDDEN_MARKERS):
            raise ValueError(f"runtime file contains a forbidden marker: {filename}")
        sources.append((member_name, content))

    try:
        with zipfile.ZipFile(
            temporary,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for member_name, content in sources:
                info = zipfile.ZipInfo(member_name, date_time=FIXED_TIMESTAMP)
                info.create_system = 3
                info.external_attr = FIXED_MODE << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(
                    info,
                    content,
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                )
        validate_release_zip(temporary)
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the deterministic ProwlarrSearch release ZIP.")
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root (defaults to the parent of scripts/).",
    )
    parser.add_argument("--output", type=Path, required=True, help="Destination ZIP path.")
    arguments = parser.parse_args()
    built = build_release(arguments.source_root, arguments.output)
    print(built)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
