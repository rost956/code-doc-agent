from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterable

from code_agent.indexer import ProjectIndexer


class LocalProjectManager:
    """Store uploaded local projects and build per-conversation code indexes."""

    def __init__(
        self,
        uploads_dir: str | Path = "data/uploads",
        indexes_dir: str | Path = "data/indexes",
    ):
        self.uploads_dir = Path(uploads_dir)
        self.indexes_dir = Path(indexes_dir)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.indexes_dir.mkdir(parents=True, exist_ok=True)

    def connect_archive(
        self,
        conversation_id: str,
        file_obj: BinaryIO,
        filename: str,
        project_name: str | None = None,
    ) -> dict[str, Any]:
        """Save a ZIP archive, extract it safely, build index and return project metadata."""
        safe_conversation_id = self._safe_name(conversation_id)
        display_name = self._display_name(project_name or filename or "uploaded-project")

        upload_root = self.uploads_dir / safe_conversation_id
        index_root = self.indexes_dir / safe_conversation_id
        project_dir = upload_root / "project"
        index_path = index_root / "code_index.json"

        self._reset_dirs(upload_root, index_root)
        project_dir.mkdir(parents=True, exist_ok=True)

        if not (filename or "").lower().endswith(".zip"):
            raise ValueError("Поддерживается загрузка архива только в формате .zip")

        content = file_obj.read()
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                self._extract_zip_safely(archive, project_dir)
        except zipfile.BadZipFile as exc:
            raise ValueError("Файл не является корректным ZIP-архивом") from exc

        index_project_root = self._detect_project_root(project_dir)
        symbol_count = self._build_index(index_project_root, index_path)

        return {
            "provider": "upload",
            "name": display_name,
            "source_type": "zip",
            "local_path": str(index_project_root),
            "index_path": str(index_path),
            "symbol_count": symbol_count,
        }

    def connect_folder(
        self,
        conversation_id: str,
        uploaded_files: Iterable[tuple[str, BinaryIO]],
        project_name: str | None = None,
    ) -> dict[str, Any]:
        """Save uploaded directory files, build index and return project metadata."""
        safe_conversation_id = self._safe_name(conversation_id)
        display_name = self._display_name(project_name or "uploaded-folder")

        upload_root = self.uploads_dir / safe_conversation_id
        index_root = self.indexes_dir / safe_conversation_id
        project_dir = upload_root / "project"
        index_path = index_root / "code_index.json"

        self._reset_dirs(upload_root, index_root)
        project_dir.mkdir(parents=True, exist_ok=True)

        saved_files = 0
        first_top_level: str | None = None
        for raw_relative_path, file_obj in uploaded_files:
            relative_path = self._safe_relative_path(raw_relative_path)
            if relative_path is None:
                continue
            if first_top_level is None and len(relative_path.parts) > 1:
                first_top_level = relative_path.parts[0]
            target_path = project_dir / Path(*relative_path.parts)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with target_path.open("wb") as target:
                shutil.copyfileobj(file_obj, target)
            saved_files += 1

        if saved_files == 0:
            raise ValueError("Не найдено файлов для загрузки")

        index_project_root = self._detect_project_root(project_dir)
        display_name = self._display_name(project_name or first_top_level or "uploaded-folder")
        symbol_count = self._build_index(index_project_root, index_path)

        return {
            "provider": "upload",
            "name": display_name,
            "source_type": "folder",
            "local_path": str(index_project_root),
            "index_path": str(index_path),
            "symbol_count": symbol_count,
        }

    def delete_conversation_resources(self, conversation_id: str) -> None:
        safe_conversation_id = self._safe_name(conversation_id)
        for root in (self.uploads_dir, self.indexes_dir):
            path = root / safe_conversation_id
            if path.exists():
                shutil.rmtree(path)

    def _reset_dirs(self, upload_root: Path, index_root: Path) -> None:
        for path in (upload_root, index_root):
            if path.exists():
                shutil.rmtree(path)
            path.mkdir(parents=True, exist_ok=True)

    def _extract_zip_safely(self, archive: zipfile.ZipFile, destination: Path) -> None:
        for info in archive.infolist():
            relative_path = self._safe_relative_path(info.filename)
            if relative_path is None:
                continue
            target_path = destination / Path(*relative_path.parts)
            if info.is_dir():
                target_path.mkdir(parents=True, exist_ok=True)
                continue
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target_path.open("wb") as target:
                shutil.copyfileobj(source, target)

    def _safe_relative_path(self, raw_path: str) -> PurePosixPath | None:
        normalized = (raw_path or "").replace("\\", "/").strip()
        if not normalized:
            return None
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Небезопасный путь в загружаемом проекте: {raw_path}")
        if any(part in {"", "."} for part in path.parts):
            return None
        # Ignore OS metadata files that are often present in archives.
        if any(part in {"__MACOSX"} for part in path.parts) or path.name in {".DS_Store", "Thumbs.db"}:
            return None
        return path

    def _detect_project_root(self, project_dir: Path) -> Path:
        children = [child for child in project_dir.iterdir() if child.name not in {"__MACOSX", ".DS_Store"}]
        if len(children) == 1 and children[0].is_dir():
            return children[0]
        return project_dir

    def _build_index(self, project_root: Path, index_path: Path) -> int:
        indexer = ProjectIndexer(project_root)
        index_data = indexer.build()
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(
            json.dumps(index_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return len(index_data.get("symbols", []))

    def _safe_name(self, value: str) -> str:
        safe = "".join(ch for ch in value if ch.isalnum() or ch in {"-", "_"})
        if not safe:
            raise ValueError("Invalid conversation id")
        return safe

    def _display_name(self, value: str) -> str:
        name = Path(value).name.strip() or "uploaded-project"
        if name.lower().endswith(".zip"):
            name = name[:-4]
        return name[:80] or "uploaded-project"
