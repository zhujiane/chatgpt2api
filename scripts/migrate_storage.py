#!/usr/bin/env python3
"""
存储后端数据迁移脚本

用法：
  python scripts/migrate_storage.py --from json --to postgres
  python scripts/migrate_storage.py --from postgres --to git
  python scripts/migrate_storage.py --export accounts.json
  python scripts/migrate_storage.py --import accounts.json
"""

import argparse
import hashlib
import importlib
import json
import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

from services.storage.factory import create_storage_backend


def _legacy_log_id(raw_line: str, line_number: int) -> str:
    payload = f"{line_number}:{raw_line}".encode("utf-8", errors="ignore")
    return hashlib.sha1(payload).hexdigest()[:24]


def _load_jsonl_logs() -> list[dict]:
    path = DATA_DIR / "logs.jsonl"
    if not path.exists():
        return []
    items = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        try:
            item = json.loads(raw_line)
        except Exception:
            continue
        if isinstance(item, dict):
            item["id"] = str(item.get("id") or _legacy_log_id(raw_line, line_number))
            items.append(item)
    return items


def _load_json_tags() -> dict[str, list[str]]:
    path = DATA_DIR / "image_tags.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(image_rel): [str(tag) for tag in tags if str(tag).strip()]
        for image_rel, tags in data.items()
        if isinstance(tags, list)
    }


def _load_logs_for_backend(backend: str) -> list[dict]:
    if backend == "json":
        return _load_jsonl_logs()
    log_module = importlib.import_module("services.log_service")
    log_module = importlib.reload(log_module)
    return log_module.log_service.list(limit=0)


def _save_logs_for_backend(logs: list[dict]) -> None:
    log_module = importlib.import_module("services.log_service")
    log_module = importlib.reload(log_module)
    log_module.log_service.replace_all(logs)


def _load_tags_for_backend(backend: str) -> dict[str, list[str]]:
    if backend == "json":
        return _load_json_tags()
    tags_module = importlib.import_module("services.image_tags_service")
    tags_module = importlib.reload(tags_module)
    return tags_module.load_tags()


def _save_tags_for_backend(tags: dict[str, list[str]]) -> None:
    tags_module = importlib.import_module("services.image_tags_service")
    tags_module = importlib.reload(tags_module)
    tags_module.save_tags(tags)


def _rebuild_image_index_for_backend(backend: str) -> int:
    if backend not in {"postgres", "postgresql", "database"}:
        return 0
    image_module = importlib.import_module("services.image_service")
    image_module = importlib.reload(image_module)
    return int(image_module.rebuild_image_index())


def export_to_json(output_file: str):
    """导出当前存储后端的数据到 JSON 文件"""
    print(f"[migrate] Exporting data to {output_file}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    storage = create_storage_backend(DATA_DIR)
    accounts = storage.load_accounts()
    auth_keys = storage.load_auth_keys()
    logs = _load_logs_for_backend(os.getenv("STORAGE_BACKEND", "json").lower().strip())
    tags = _load_tags_for_backend(os.getenv("STORAGE_BACKEND", "json").lower().strip())
    
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "accounts": accounts,
                "auth_keys": auth_keys,
                "logs": logs,
                "image_tags": tags,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    
    print(
        f"[migrate] Exported {len(accounts)} accounts, {len(auth_keys)} auth keys, "
        f"{len(logs)} logs, {len(tags)} image tag entries to {output_file}"
    )


def import_from_json(input_file: str):
    """从 JSON 文件导入数据到当前存储后端"""
    print(f"[migrate] Importing data from {input_file}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"[migrate] Error: File not found: {input_file}")
        sys.exit(1)
    
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            accounts = payload
            auth_keys = []
            logs = []
            tags = {}
        elif isinstance(payload, dict):
            accounts = payload.get("accounts") if isinstance(payload.get("accounts"), list) else []
            auth_keys = payload.get("auth_keys") if isinstance(payload.get("auth_keys"), list) else []
            logs = payload.get("logs") if isinstance(payload.get("logs"), list) else []
            tags = payload.get("image_tags") if isinstance(payload.get("image_tags"), dict) else {}
        else:
            print("[migrate] Error: Invalid JSON format, expected object or array")
            sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"[migrate] Error: Invalid JSON: {e}")
        sys.exit(1)
    
    storage = create_storage_backend(DATA_DIR)
    storage.save_accounts(accounts)
    storage.save_auth_keys(auth_keys)
    _save_logs_for_backend(logs)
    _save_tags_for_backend(tags)
    indexed_images = _rebuild_image_index_for_backend(os.getenv("STORAGE_BACKEND", "json").lower().strip())
    
    print(
        f"[migrate] Imported {len(accounts)} accounts, {len(auth_keys)} auth keys, "
        f"{len(logs)} logs, {len(tags)} image tag entries, indexed {indexed_images} images"
    )


def migrate_data(from_backend: str, to_backend: str):
    """从一个存储后端迁移到另一个"""
    print(f"[migrate] Migrating from {from_backend} to {to_backend}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # 保存原始环境变量
    original_backend = os.environ.get("STORAGE_BACKEND")
    
    try:
        # 从源后端读取数据
        os.environ["STORAGE_BACKEND"] = from_backend
        from_storage = create_storage_backend(DATA_DIR)
        accounts = from_storage.load_accounts()
        auth_keys = from_storage.load_auth_keys()
        logs = _load_logs_for_backend(from_backend)
        tags = _load_tags_for_backend(from_backend)
        print(
            f"[migrate] Loaded {len(accounts)} accounts, {len(auth_keys)} auth keys, "
            f"{len(logs)} logs, {len(tags)} image tag entries from {from_backend}"
        )
        
        # 写入目标后端
        os.environ["STORAGE_BACKEND"] = to_backend
        to_storage = create_storage_backend(DATA_DIR)
        to_storage.save_accounts(accounts)
        to_storage.save_auth_keys(auth_keys)
        _save_logs_for_backend(logs)
        _save_tags_for_backend(tags)
        indexed_images = _rebuild_image_index_for_backend(to_backend)
        print(
            f"[migrate] Saved {len(accounts)} accounts, {len(auth_keys)} auth keys, "
            f"{len(logs)} logs, {len(tags)} image tag entries to {to_backend}; "
            f"indexed {indexed_images} images"
        )
        
        print(f"[migrate] Migration completed successfully!")
        
    finally:
        # 恢复原始环境变量
        if original_backend:
            os.environ["STORAGE_BACKEND"] = original_backend
        elif "STORAGE_BACKEND" in os.environ:
            del os.environ["STORAGE_BACKEND"]


def main():
    parser = argparse.ArgumentParser(
        description="ChatGPT2API 存储后端数据迁移工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 从 JSON 迁移到 PostgreSQL
  python scripts/migrate_storage.py --from json --to postgres
  
  # 从 PostgreSQL 迁移到 Git
  python scripts/migrate_storage.py --from postgres --to git
  
  # 导出当前数据到 JSON 文件
  python scripts/migrate_storage.py --export backup.json
  
  # 从 JSON 文件导入数据
  python scripts/migrate_storage.py --import backup.json

环境变量:
  STORAGE_BACKEND  - 存储后端类型 (json, sqlite, postgres, git)
  DATABASE_URL     - 数据库连接字符串
  GIT_REPO_URL     - Git 仓库地址
  GIT_TOKEN        - Git 访问令牌
        """
    )
    
    parser.add_argument(
        "--from",
        dest="from_backend",
        choices=["json", "sqlite", "postgres", "git"],
        help="源存储后端",
    )
    parser.add_argument(
        "--to",
        dest="to_backend",
        choices=["json", "sqlite", "postgres", "git"],
        help="目标存储后端",
    )
    parser.add_argument(
        "--export",
        dest="export_file",
        metavar="FILE",
        help="导出数据到 JSON 文件",
    )
    parser.add_argument(
        "--import",
        dest="import_file",
        metavar="FILE",
        help="从 JSON 文件导入数据",
    )
    
    args = parser.parse_args()
    
    # 检查参数
    if args.from_backend and args.to_backend:
        migrate_data(args.from_backend, args.to_backend)
    elif args.export_file:
        export_to_json(args.export_file)
    elif args.import_file:
        import_from_json(args.import_file)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
