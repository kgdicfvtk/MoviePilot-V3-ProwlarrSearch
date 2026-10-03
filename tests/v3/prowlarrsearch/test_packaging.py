from __future__ import annotations

import ast
import hashlib
import importlib
import json
import stat
import zipfile
from pathlib import Path

import pytest

from app.plugins.prowlarrsearch import ProwlarrSearch
from scripts.build_release import build_release, validate_release_zip


SOURCE_ROOT = Path(__file__).parents[3]
RUNTIME_ROOT = SOURCE_ROOT / "plugins.v3" / "prowlarrsearch"
EXPECTED_MEMBERS = (
    "prowlarrsearch/__init__.py",
    "prowlarrsearch/client.py",
    "prowlarrsearch/config.py",
    "prowlarrsearch/mapper.py",
    "prowlarrsearch/relay.py",
)


def _write_archive(
    path: Path,
    members: dict[str, bytes],
    *,
    symlink_name: str | None = None,
) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            mode = stat.S_IFLNK | 0o777 if name == symlink_name else stat.S_IFREG | 0o644
            info.external_attr = mode << 16
            archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED)


def _valid_member_data() -> dict[str, bytes]:
    return {name: b"# safe runtime source\n" for name in EXPECTED_MEMBERS}


def test_manifest_matches_runtime_identity_and_release_contract() -> None:
    manifest = json.loads((SOURCE_ROOT / "package.v3.json").read_text(encoding="utf-8"))

    assert set(manifest) == {"ProwlarrSearch"}
    entry = manifest["ProwlarrSearch"]
    assert entry["name"] == "Prowlarr 搜索"
    assert entry["author"] == "Codex"
    assert entry["labels"] == "搜索,Prowlarr"
    assert entry["level"] == 1
    assert entry["version"] == ProwlarrSearch.plugin_version == "1.0.0"
    assert entry["system_version"] == ">=3.1.0"
    assert entry["release"] is True
    assert set(entry["history"]) == {"v1.0.0"}
    assert RUNTIME_ROOT.is_dir()
    assert tuple(sorted(path.name for path in RUNTIME_ROOT.glob("*.py"))) == (
        "__init__.py",
        "client.py",
        "config.py",
        "mapper.py",
        "relay.py",
    )


def test_readme_documents_configuration_installation_scope_and_acceptance() -> None:
    readme = (SOURCE_ROOT / "README.md").read_text(encoding="utf-8")

    assert "http://prowlarr:9696" in readme
    for field in ("enabled", "base_url", "api_key", "timeout", "page_size"):
        assert f"`{field}`" in readme
    assert "MoviePilot 官方用户认证" in readme
    assert "IYUU" in readme
    assert "不需要" in readme
    assert "/mnt/user/downloads/qbittorrent" in readme
    assert "/mnt/user/media/video" in readme
    assert "/mnt/user/media/links" in readme
    assert "Jellyfin" in readme
    assert "不会修改" in readme
    assert "PLUGIN_LOCAL_REPO_PATHS" in readme
    assert "第三方插件仓库" in readme
    assert "GitHub Release" in readme
    assert "实机验收" in readme
    for number in range(1, 10):
        assert f"{number}. " in readme
    assert "不宣称当前 MoviePilot UI 可以直接上传本地 ZIP" in readme
    assert "运行中的 UI 明确提供" in readme


def test_release_build_is_byte_reproducible_and_metadata_normalized(tmp_path: Path) -> None:
    first = build_release(SOURCE_ROOT, tmp_path / "first.zip")
    second = build_release(SOURCE_ROOT, tmp_path / "second.zip")

    first_bytes = first.read_bytes()
    second_bytes = second.read_bytes()
    assert first_bytes == second_bytes
    assert hashlib.sha256(first_bytes).hexdigest() == hashlib.sha256(second_bytes).hexdigest()
    assert validate_release_zip(first) == EXPECTED_MEMBERS
    assert validate_release_zip(second) == EXPECTED_MEMBERS

    with zipfile.ZipFile(first) as archive:
        infos = archive.infolist()
    assert tuple(info.filename for info in infos) == EXPECTED_MEMBERS
    assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in infos)
    assert all(info.create_system == 3 for info in infos)
    assert all(info.external_attr >> 16 == stat.S_IFREG | 0o644 for info in infos)
    assert all(info.compress_type == zipfile.ZIP_DEFLATED for info in infos)


@pytest.mark.parametrize(
    "extra_name",
    [
        "README.md",
        "package.v3.json",
        "scripts/build_release.py",
        "tests/test_release.py",
        "prowlarrsearch/__pycache__/relay.pyc",
        "prowlarrsearch/.env",
        "prowlarrsearch/.git/config",
        "../escape.py",
        "/absolute.py",
        "C:/absolute.py",
    ],
)
def test_release_validator_rejects_extra_or_unsafe_members(
    tmp_path: Path, extra_name: str
) -> None:
    members = _valid_member_data()
    members[extra_name] = b"unsafe"
    archive = tmp_path / "invalid.zip"
    _write_archive(archive, members)

    with pytest.raises(ValueError):
        validate_release_zip(archive)


def test_release_validator_rejects_missing_runtime_file(tmp_path: Path) -> None:
    members = _valid_member_data()
    members.pop("prowlarrsearch/relay.py")
    archive = tmp_path / "missing.zip"
    _write_archive(archive, members)

    with pytest.raises(ValueError):
        validate_release_zip(archive)


def test_release_validator_rejects_duplicate_member(tmp_path: Path) -> None:
    archive = tmp_path / "duplicate.zip"
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(archive, "w") as output:
            for name in EXPECTED_MEMBERS:
                output.writestr(name, b"safe")
            output.writestr(EXPECTED_MEMBERS[0], b"duplicate")

    with pytest.raises(ValueError):
        validate_release_zip(archive)


def test_release_validator_rejects_symlink(tmp_path: Path) -> None:
    archive = tmp_path / "symlink.zip"
    _write_archive(
        archive,
        _valid_member_data(),
        symlink_name="prowlarrsearch/relay.py",
    )

    with pytest.raises(ValueError):
        validate_release_zip(archive)


@pytest.mark.parametrize(
    "marker",
    [
        b"TEST_PROWLARR_KEY_DO_NOT_PACKAGE",
        b"-----BEGIN PRIVATE KEY-----",
        b"-----BEGIN OPENSSH PRIVATE KEY-----",
    ],
)
def test_release_validator_rejects_secret_markers(tmp_path: Path, marker: bytes) -> None:
    members = _valid_member_data()
    members["prowlarrsearch/client.py"] += marker
    archive = tmp_path / "secret.zip"
    _write_archive(archive, members)

    with pytest.raises(ValueError):
        validate_release_zip(archive)


def test_release_builder_never_packages_non_runtime_files(tmp_path: Path) -> None:
    archive_path = build_release(SOURCE_ROOT, tmp_path / "release.zip")

    with zipfile.ZipFile(archive_path) as archive:
        names = tuple(info.filename for info in archive.infolist())
        content = b"\n".join(archive.read(name) for name in names)

    assert names == EXPECTED_MEMBERS
    assert b"TEST_PROWLARR_KEY_DO_NOT_PACKAGE" not in content
    assert all("README" not in name for name in names)


def test_runtime_files_compile_and_import_through_production_namespace() -> None:
    for source in sorted(RUNTIME_ROOT.glob("*.py")):
        compile(source.read_text(encoding="utf-8"), str(source), "exec")

    plugin_module = importlib.import_module("app.plugins.prowlarrsearch")
    assert plugin_module.__name__ == "app.plugins.prowlarrsearch"
    assert plugin_module.ProwlarrSearch is ProwlarrSearch


def test_runtime_uses_only_public_v3_app_namespace_and_has_no_media_side_effects() -> None:
    forbidden_source_fragments = (
        "app.core",
        "app.modules",
        "monkeypatch",
        "qbittorrent",
        "/mnt/user/",
        "PLUGIN_LOCAL_REPO_PATHS",
        "os.link(",
        "shutil.copy",
        ".copy2(",
        ".rmtree(",
    )

    for source in sorted(RUNTIME_ROOT.glob("*.py")):
        text = source.read_text(encoding="utf-8")
        lowered = text.lower()
        assert all(fragment.lower() not in lowered for fragment in forbidden_source_fragments)

        tree = ast.parse(text, filename=str(source))
        imported_app_modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_app_modules.extend(
                    alias.name for alias in node.names if alias.name.startswith("app.")
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("app."):
                    imported_app_modules.append(node.module)
        assert all(name.startswith("app.sdk.") for name in imported_app_modules)


def test_secret_sentinel_is_confined_to_test_sources() -> None:
    sentinel = "TEST_PROWLARR_KEY_DO_NOT_PACKAGE"
    non_test_sources = (
        SOURCE_ROOT / "README.md",
        SOURCE_ROOT / "package.v3.json",
        SOURCE_ROOT / "pyproject.toml",
        SOURCE_ROOT / "scripts" / "build_release.py",
        *sorted(RUNTIME_ROOT.glob("*.py")),
    )

    for source in non_test_sources:
        assert sentinel not in source.read_text(encoding="utf-8")
