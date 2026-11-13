"""Bounded ZIP extraction into the persistent external-plugin directory."""
from __future__ import annotations

import ast
import io
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from core.paths import DATA_DIR
from domain.plugins.plugin_name import normalize_plugin_name

MAX_UPLOAD = 20 * 1024 * 1024
MAX_EXPANDED = 50 * 1024 * 1024
MAX_FILES = 512


def plugin_upload_root() -> Path:
    return Path(DATA_DIR) / "plugins"


def validate_name(name: str) -> str:
    normalized = normalize_plugin_name(name)
    if not normalized or normalized.startswith("_") or len(normalized) > 64:
        raise ValueError("插件名仅允许字母、数字和下划线，不可以下划线开头，最多 64 字符")
    return normalized


def stage_package(payload: bytes, name: str = "") -> tuple[str, Path]:
    if len(payload) > MAX_UPLOAD:
        raise ValueError("ZIP 不能超过 20 MiB")
    root = plugin_upload_root()
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError("插件存储目录不能是符号链接")
    stage = Path(tempfile.mkdtemp(prefix=".upload-", dir=root))
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            files = archive.infolist()
            if not files or len(files) > MAX_FILES:
                raise ValueError("ZIP 为空或文件数超过 512")
            paths = []
            total = 0
            seen = set()
            for item in files:
                path = PurePosixPath(item.filename)
                mode = item.external_attr >> 16
                if ("\\" in item.filename or path.is_absolute() or ".." in path.parts
                        or not path.parts or ":" in path.parts[0]
                        or stat.S_ISLNK(mode) or item.flag_bits & 1):
                    raise ValueError("ZIP 包含不安全路径、符号链接或加密文件")
                if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise ValueError("ZIP 包含不支持的文件类型")
                if "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
                    continue
                if path in seen:
                    raise ValueError("ZIP 包含重复路径")
                seen.add(path)
                total += item.file_size
                if total > MAX_EXPANDED or item.file_size > max(1024 * 1024, item.compress_size * 200):
                    raise ValueError("ZIP 解压体积过大")
                paths.append((item, path))
            regular = [path for item, path in paths if not item.is_dir()]
            if PurePosixPath("__init__.py") in regular:
                prefix = None
            else:
                roots = {p.parts[0] for p in regular}
                if len(roots) != 1:
                    raise ValueError("ZIP 必须包含一个插件目录，或在根目录提供 __init__.py")
                prefix = next(iter(roots))
                if PurePosixPath(prefix, "__init__.py") not in regular:
                    raise ValueError("插件缺少 __init__.py 入口")
            inferred = prefix or ""
            plugin_name = validate_name(name or inferred)
            for item, path in paths:
                relative = PurePosixPath(*path.parts[1:]) if prefix else path
                if not relative.parts:
                    continue
                target = stage.joinpath(*relative.parts)
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                data = archive.read(item)
                if target.suffix == ".py":
                    ast.parse(data, filename=str(relative))
                target.write_bytes(data)
                target.chmod(0o600)
            return plugin_name, stage
    except (zipfile.BadZipFile, SyntaxError, UnicodeError) as exc:
        shutil.rmtree(stage, ignore_errors=True)
        raise ValueError("ZIP 损坏或 Python 语法错误") from exc
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def replace_package(name: str, stage: Path) -> Path | None:
    root = plugin_upload_root()
    target = root / validate_name(name)
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise ValueError("插件目标路径不安全")
    backup = None
    if target.exists():
        backup = Path(tempfile.mkdtemp(prefix=".backup-", dir=root))
        backup.rmdir()
        os.replace(target, backup)
    try:
        os.replace(stage, target)
    except Exception:
        if backup:
            os.replace(backup, target)
        raise
    return backup


def restore_package(name: str, backup: Path | None) -> None:
    target = plugin_upload_root() / validate_name(name)
    shutil.rmtree(target, ignore_errors=True)
    if backup:
        os.replace(backup, target)

