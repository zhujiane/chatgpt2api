from __future__ import annotations

import json
import os
from pathlib import Path

from sqlalchemy import Column, String, Text, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from services.config import DATA_DIR

TAGS_FILE = DATA_DIR / "image_tags.json"

Base = declarative_base()


class ImageTagModel(Base):
    __tablename__ = "image_tags"

    image_rel = Column(String(1024), primary_key=True)
    tags = Column(Text, nullable=False)


def _database_url() -> str:
    backend = os.getenv("STORAGE_BACKEND", "json").lower().strip()
    database_url = os.getenv("DATABASE_URL", "").strip()
    if backend in {"postgres", "postgresql", "database"} and database_url:
        return database_url
    return ""


class _DatabaseTags:
    def __init__(self, database_url: str):
        self.engine = create_engine(database_url, pool_pre_ping=True, pool_recycle=3600)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def load(self) -> dict[str, list[str]]:
        session = self.Session()
        try:
            result: dict[str, list[str]] = {}
            for row in session.query(ImageTagModel).all():
                try:
                    tags = json.loads(row.tags)
                except Exception:
                    continue
                if isinstance(tags, list):
                    result[str(row.image_rel)] = [str(item) for item in tags if str(item).strip()]
            return result
        finally:
            session.close()

    def save(self, data: dict[str, list[str]]) -> None:
        session = self.Session()
        try:
            session.query(ImageTagModel).delete()
            for image_rel, tags in data.items():
                cleaned = list(dict.fromkeys(str(t).strip() for t in tags if str(t).strip()))
                if cleaned:
                    session.add(ImageTagModel(image_rel=str(image_rel), tags=json.dumps(cleaned, ensure_ascii=False)))
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


_database_tags: _DatabaseTags | None = None


def _get_database_tags() -> _DatabaseTags | None:
    global _database_tags
    database_url = _database_url()
    if not database_url:
        return None
    if _database_tags is None:
        _database_tags = _DatabaseTags(database_url)
    return _database_tags


def _ensure_file() -> None:
    TAGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not TAGS_FILE.exists():
        TAGS_FILE.write_text("{}", encoding="utf-8")


def load_tags() -> dict[str, list[str]]:
    database_tags = _get_database_tags()
    if database_tags is not None:
        return database_tags.load()

    _ensure_file()
    try:
        data = json.loads(TAGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_tags(data: dict[str, list[str]]) -> None:
    database_tags = _get_database_tags()
    if database_tags is not None:
        database_tags.save(data)
        return

    _ensure_file()
    TAGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def get_tags(image_rel: str) -> list[str]:
    return load_tags().get(image_rel, [])


def set_tags(image_rel: str, tags: list[str]) -> list[str]:
    data = load_tags()
    cleaned = list(dict.fromkeys(t.strip() for t in tags if t.strip()))
    if cleaned:
        data[image_rel] = cleaned
    else:
        data.pop(image_rel, None)
    save_tags(data)
    return cleaned


def remove_tags(image_rel: str) -> None:
    data = load_tags()
    if data.pop(image_rel, None) is not None:
        save_tags(data)


def delete_tag(tag: str) -> int:
    """从所有图片中删除指定标签，返回受影响的图片数。"""
    data = load_tags()
    count = 0
    for rel in list(data):
        if tag in data[rel]:
            data[rel] = [t for t in data[rel] if t != tag]
            if not data[rel]:
                del data[rel]
            count += 1
    if count > 0:
        save_tags(data)
    return count


def get_all_tags() -> list[str]:
    data = load_tags()
    seen: set[str] = set()
    result: list[str] = []
    for tags in data.values():
        for t in tags:
            if t not in seen:
                seen.add(t)
                result.append(t)
    return result
