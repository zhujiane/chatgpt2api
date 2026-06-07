from __future__ import annotations

import io
import os
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from PIL import Image, ImageOps
from sqlalchemy import Column, Float, Integer, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from services.config import config
from services.image_tags_service import load_tags, remove_tags

THUMBNAIL_SIZE = (320, 320)

Base = declarative_base()


class ImageModel(Base):
    __tablename__ = "images"

    path = Column(String(1024), primary_key=True)
    name = Column(String(512), nullable=False)
    date = Column(String(10), nullable=False, index=True)
    size = Column(Integer, nullable=False)
    created_at = Column(String(32), nullable=False, index=True)
    mtime = Column(Float, nullable=False, index=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)


def _database_url() -> str:
    backend = os.getenv("STORAGE_BACKEND", "json").lower().strip()
    database_url = os.getenv("DATABASE_URL", "").strip()
    if backend in {"postgres", "postgresql", "database"} and database_url:
        return database_url
    return ""


class ImageIndex:
    def __init__(self, database_url: str):
        self.engine = create_engine(database_url, pool_pre_ping=True, pool_recycle=3600)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    @staticmethod
    def _item_from_path(path: Path, root: Path) -> dict[str, object]:
        rel = path.relative_to(root).as_posix()
        parts = rel.split("/")
        stat = path.stat()
        day = "-".join(parts[:3]) if len(parts) >= 4 else datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d")
        dimensions = _image_dimensions(path)
        return {
            "path": rel,
            "name": path.name,
            "date": day,
            "size": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "mtime": stat.st_mtime,
            "width": dimensions[0] if dimensions else None,
            "height": dimensions[1] if dimensions else None,
        }

    @staticmethod
    def _row_to_item(row: ImageModel) -> dict[str, object]:
        item: dict[str, object] = {
            "rel": row.path,
            "path": row.path,
            "name": row.name,
            "date": row.date,
            "size": row.size,
            "created_at": row.created_at,
        }
        if row.width:
            item["width"] = row.width
        if row.height:
            item["height"] = row.height
        return item

    def upsert_path(self, relative_path: str) -> None:
        rel = _safe_relative_path(relative_path)
        root = config.images_dir.resolve()
        path = (root / rel).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            return
        if not path.is_file():
            return
        item = self._item_from_path(path, root)
        session = self.Session()
        try:
            session.merge(ImageModel(**item))
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def rebuild(self) -> int:
        root = config.images_dir.resolve()
        items = [self._item_from_path(path, root) for path in root.rglob("*") if path.is_file()]
        session = self.Session()
        try:
            session.query(ImageModel).delete()
            for item in items:
                session.add(ImageModel(**item))
            session.commit()
            return len(items)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def count(self) -> int:
        session = self.Session()
        try:
            return int(session.query(ImageModel).count())
        finally:
            session.close()

    def list(self, start_date: str = "", end_date: str = "") -> list[dict[str, object]]:
        session = self.Session()
        try:
            query = session.query(ImageModel)
            if start_date:
                query = query.filter(ImageModel.date >= start_date)
            if end_date:
                query = query.filter(ImageModel.date <= end_date)
            return [self._row_to_item(row) for row in query.order_by(ImageModel.created_at.desc()).all()]
        finally:
            session.close()

    def remove(self, paths: list[str]) -> int:
        cleaned = [_safe_relative_path(path) for path in paths if str(path or "").strip()]
        if not cleaned:
            return 0
        session = self.Session()
        try:
            removed = session.query(ImageModel).filter(ImageModel.path.in_(cleaned)).delete(synchronize_session=False)
            session.commit()
            return int(removed or 0)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def cleanup_old(self, retention_days: int) -> int:
        cutoff = datetime.now().timestamp() - max(1, retention_days) * 86400
        root = config.images_dir.resolve()
        session = self.Session()
        try:
            rows = session.query(ImageModel).filter(ImageModel.mtime < cutoff).all()
            removed = 0
            for row in rows:
                path = (root / row.path).resolve()
                try:
                    path.relative_to(root)
                except ValueError:
                    continue
                if path.is_file():
                    path.unlink()
                    removed += 1
                session.delete(row)
            session.commit()
            _cleanup_empty_dirs(root)
            return removed
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


_image_index: ImageIndex | None = None


def _get_image_index() -> ImageIndex | None:
    global _image_index
    database_url = _database_url()
    if not database_url:
        return None
    if _image_index is None:
        _image_index = ImageIndex(database_url)
    return _image_index


def _cleanup_empty_dirs(root: Path) -> None:
    for path in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass


def _safe_relative_path(path: str) -> str:
    value = str(path or "").strip().replace("\\", "/").lstrip("/")
    if not value:
        raise HTTPException(status_code=404, detail="image not found")
    parts = Path(value).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise HTTPException(status_code=404, detail="image not found")
    return Path(*parts).as_posix()


def _safe_image_path(relative_path: str) -> Path:
    rel = _safe_relative_path(relative_path)
    root = config.images_dir.resolve()
    path = (root / rel).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="image not found") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="image not found")
    return path


def _thumbnail_path(relative_path: str) -> Path:
    rel = _safe_relative_path(relative_path)
    return config.image_thumbnails_dir / f"{rel}.png"


def thumbnail_url(base_url: str, relative_path: str) -> str:
    return f"{base_url.rstrip('/')}/image-thumbnails/{_safe_relative_path(relative_path)}"


def _image_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with Image.open(path) as image:
            return image.size
    except Exception:
        return None


def ensure_thumbnail(relative_path: str) -> Path:
    source = _safe_image_path(relative_path)
    target = _thumbnail_path(relative_path)
    source_mtime = source.stat().st_mtime
    if target.exists() and target.stat().st_mtime >= source_mtime:
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            image.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
            image.save(target, format="PNG", optimize=True)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail="failed to create thumbnail") from exc
    return target


def get_thumbnail_response(relative_path: str) -> FileResponse:
    return FileResponse(ensure_thumbnail(relative_path))


def get_image_download_response(relative_path: str) -> FileResponse:
    path = _safe_image_path(relative_path)
    return FileResponse(path, filename=path.name)


def cleanup_image_thumbnails() -> int:
    thumbnails_root = config.image_thumbnails_dir
    images_root = config.images_dir
    removed = 0
    for path in thumbnails_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(thumbnails_root).as_posix()
        if not rel.endswith(".png") or not (images_root / rel[:-4]).exists():
            path.unlink()
            removed += 1
    _cleanup_empty_dirs(thumbnails_root)
    return removed


def _image_items(start_date: str = "", end_date: str = "") -> list[dict[str, object]]:
    index = _get_image_index()
    if index is not None:
        if index.count() == 0 and any(config.images_dir.rglob("*")):
            index.rebuild()
        return index.list(start_date, end_date)

    items = []
    root = config.images_dir
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        parts = rel.split("/")
        day = "-".join(parts[:3]) if len(parts) >= 4 else datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
        if start_date and day < start_date:
            continue
        if end_date and day > end_date:
            continue
        dimensions = _image_dimensions(path)
        items.append({
            "rel": rel,
            "path": rel,
            "name": path.name,
            "date": day,
            "size": path.stat().st_size,
            "created_at": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            **({"width": dimensions[0], "height": dimensions[1]} if dimensions else {}),
        })
    items.sort(key=lambda item: str(item["created_at"]), reverse=True)
    return items


def list_images(base_url: str, start_date: str = "", end_date: str = "") -> dict[str, object]:
    index = _get_image_index()
    if index is not None:
        index.cleanup_old(config.image_retention_days)
    else:
        config.cleanup_old_images()
        cleanup_image_thumbnails()
    all_tags = load_tags()
    items = [
        {
            **item,
            "url": f"{base_url.rstrip('/')}/images/{item['path']}",
            "thumbnail_url": thumbnail_url(base_url, str(item["path"])),
            "tags": all_tags.get(str(item["path"]), []),
        }
        for item in _image_items(start_date, end_date)
    ]
    groups: dict[str, list[dict[str, object]]] = {}
    for item in items:
        groups.setdefault(str(item["date"]), []).append(item)
    return {"items": items, "groups": [{"date": key, "items": value} for key, value in groups.items()]}


def delete_images(paths: list[str] | None = None, start_date: str = "", end_date: str = "", all_matching: bool = False) -> dict[str, int]:
    root = config.images_dir.resolve()
    targets = [str(item["path"]) for item in _image_items(start_date, end_date)] if all_matching else (paths or [])
    removed = 0
    for item in targets:
        path = (root / item).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if path.is_file():
            path.unlink()
            for thumbnail in (_thumbnail_path(item), config.image_thumbnails_dir / _safe_relative_path(item)):
                if thumbnail.is_file():
                    thumbnail.unlink()
            remove_tags(item)
            removed += 1
    _cleanup_empty_dirs(root)
    _cleanup_empty_dirs(config.image_thumbnails_dir)
    index = _get_image_index()
    if index is not None:
        index.remove(targets)
    return {"removed": removed}


def register_image(relative_path: str) -> None:
    index = _get_image_index()
    if index is None:
        return
    index.upsert_path(relative_path)


def rebuild_image_index() -> int:
    index = _get_image_index()
    if index is None:
        return 0
    return index.rebuild()


def download_images_zip(paths: list[str]) -> io.BytesIO:
    root = config.images_dir.resolve()
    buf = io.BytesIO()
    added = 0
    used_names: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in paths:
            rel = _safe_relative_path(item)
            path = (root / rel).resolve()
            try:
                path.relative_to(root)
            except ValueError:
                continue
            if not path.is_file():
                continue
            name = path.name
            if name in used_names:
                stem = path.stem
                suffix = path.suffix
                counter = 2
                while f"{stem}_{counter}{suffix}" in used_names:
                    counter += 1
                name = f"{stem}_{counter}{suffix}"
            used_names.add(name)
            zf.write(path, name)
            added += 1
    if added == 0:
        raise HTTPException(status_code=404, detail="no images found")
    buf.seek(0)
    return buf
