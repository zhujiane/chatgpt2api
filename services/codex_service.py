from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from services.config import BASE_DIR, DATA_DIR


CODEX_ROOT = DATA_DIR / "codex"
CODEX_USERS_DIR = CODEX_ROOT / "users"
CODEX_DEFAULT_FILE = CODEX_USERS_DIR / "default.json"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_user_id(value: str) -> str:
    user_id = str(value or "").strip()
    if not re.fullmatch(r"[a-zA-Z0-9_-]{6,64}", user_id):
        raise ValueError("invalid codex user id")
    return user_id


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _codex_command() -> list[str]:
    command = os.getenv("CODEX_CLI_COMMAND", "codex").strip() or "codex"
    resolved = shutil.which(command) or command
    if os.name == "nt" and resolved.lower().endswith(".ps1"):
        powershell = shutil.which("powershell.exe") or shutil.which("powershell") or "powershell.exe"
        return [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", resolved]
    return [resolved]


def _usage_from_text(text: str) -> dict[str, Any]:
    usage: dict[str, Any] = {
        "five_hour_remaining": None,
        "seven_day_remaining": None,
        "raw": text.strip(),
    }
    patterns = [
        ("five_hour_remaining", r"(?:5\s*h|five[-_\s]?hour).*?(\d+(?:\.\d+)?)"),
        ("seven_day_remaining", r"(?:7\s*d|seven[-_\s]?day).*?(\d+(?:\.\d+)?)"),
    ]
    lowered = text.lower()
    for key, pattern in patterns:
        match = re.search(pattern, lowered, re.S)
        if match:
            usage[key] = match.group(1)
    return usage


def _usage_from_auth_json(auth_json: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(auth_json, ensure_ascii=False)
    usage = _usage_from_text(text)
    usage["raw"] = ""
    return usage


class CodexService:
    def __init__(self) -> None:
        CODEX_USERS_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._login_processes: dict[str, subprocess.Popen[str]] = {}

    def _user_dir(self, user_id: str) -> Path:
        return CODEX_USERS_DIR / _safe_user_id(user_id)

    def _metadata_path(self, user_id: str) -> Path:
        return self._user_dir(user_id) / "metadata.json"

    def _auth_path(self, user_id: str) -> Path:
        return self._user_dir(user_id) / "auth.json"

    def _load_metadata(self, user_id: str) -> dict[str, Any]:
        metadata = _read_json(self._metadata_path(user_id))
        if not metadata:
            metadata = {
                "id": user_id,
                "name": user_id,
                "enabled": True,
                "status": "unknown",
                "created_at": None,
                "updated_at": None,
            }
        metadata["id"] = user_id
        metadata["auth_file_exists"] = self._auth_path(user_id).exists()
        if metadata["auth_file_exists"] and metadata.get("status") == "login_pending":
            metadata["status"] = "normal"
            metadata["last_login_at"] = metadata.get("last_login_at") or _now()
            _write_json(self._metadata_path(user_id), metadata)
        metadata["is_default"] = self.get_default_user_id() == user_id
        metadata.setdefault("usage", {
            "five_hour_remaining": None,
            "seven_day_remaining": None,
            "raw": "",
        })
        return metadata

    def _save_metadata(self, user_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(metadata)
        metadata["id"] = user_id
        metadata["auth_file_exists"] = self._auth_path(user_id).exists()
        metadata["updated_at"] = _now()
        _write_json(self._metadata_path(user_id), metadata)
        return self._load_metadata(user_id)

    def get_default_user_id(self) -> str:
        data = _read_json(CODEX_DEFAULT_FILE)
        return str(data.get("user_id") or "").strip()

    def list_users(self) -> list[dict[str, Any]]:
        CODEX_USERS_DIR.mkdir(parents=True, exist_ok=True)
        users = []
        for path in CODEX_USERS_DIR.iterdir():
            if path.is_dir():
                try:
                    users.append(self._load_metadata(path.name))
                except ValueError:
                    continue
        return sorted(users, key=lambda item: str(item.get("created_at") or ""), reverse=True)

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        user_id = _safe_user_id(user_id)
        if not self._user_dir(user_id).exists():
            return None
        return self._load_metadata(user_id)

    def start_login(self, name: str = "", mode: str = "browser") -> dict[str, Any]:
        user_id = f"codex_{uuid.uuid4().hex[:12]}"
        user_dir = self._user_dir(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        metadata = {
            "id": user_id,
            "name": str(name or "").strip() or f"Codex {datetime.now().strftime('%m%d %H:%M')}",
            "enabled": True,
            "status": "login_pending",
            "login_mode": "device" if mode == "device" else "browser",
            "created_at": _now(),
            "updated_at": _now(),
            "last_login_at": None,
            "last_status_checked_at": None,
            "login_output": "",
            "login_error": "",
            "usage": {
                "five_hour_remaining": None,
                "seven_day_remaining": None,
                "raw": "",
            },
        }
        _write_json(self._metadata_path(user_id), metadata)

        command = [*_codex_command(), "login"]
        if mode == "device":
            command.append("--device-auth")
        env = {**os.environ, "CODEX_HOME": str(user_dir)}
        try:
            process = subprocess.Popen(
                command,
                cwd=str(BASE_DIR),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except Exception as exc:
            metadata["status"] = "error"
            metadata["login_error"] = str(exc)
            return self._save_metadata(user_id, metadata)

        with self._lock:
            self._login_processes[user_id] = process
        threading.Thread(target=self._watch_login, args=(user_id, process), daemon=True).start()
        return self._load_metadata(user_id)

    def _watch_login(self, user_id: str, process: subprocess.Popen[str]) -> None:
        try:
            stdout, stderr = process.communicate()
            return_code = process.returncode
        except Exception as exc:
            stdout, stderr, return_code = "", str(exc), -1
        finally:
            with self._lock:
                current = self._login_processes.get(user_id)
                if current is process:
                    self._login_processes.pop(user_id, None)

        metadata = self._load_metadata(user_id)
        metadata["login_output"] = stdout or ""
        metadata["login_error"] = stderr or ""
        metadata["last_login_at"] = _now()
        if self._auth_path(user_id).exists() and return_code == 0:
            metadata["status"] = "normal"
            metadata["usage"] = self._usage_for_user(user_id, stdout or "")
            if not self.get_default_user_id():
                self.set_default_user(user_id)
        elif self._auth_path(user_id).exists():
            metadata["status"] = "normal"
            metadata["usage"] = self._usage_for_user(user_id, f"{stdout}\n{stderr}")
        else:
            metadata["status"] = "error"
        self._save_metadata(user_id, metadata)

    def _usage_for_user(self, user_id: str, text: str = "") -> dict[str, Any]:
        usage = _usage_from_text(text)
        auth_data = _read_json(self._auth_path(user_id))
        auth_usage = _usage_from_auth_json(auth_data) if auth_data else {}
        return {
            "five_hour_remaining": usage.get("five_hour_remaining") or auth_usage.get("five_hour_remaining"),
            "seven_day_remaining": usage.get("seven_day_remaining") or auth_usage.get("seven_day_remaining"),
            "raw": usage.get("raw") or auth_usage.get("raw") or "",
        }

    def refresh_status(self, user_id: str) -> dict[str, Any]:
        user_id = _safe_user_id(user_id)
        user = self.get_user(user_id)
        if user is None:
            raise FileNotFoundError("codex user not found")
        if not self._auth_path(user_id).exists():
            user["status"] = "error"
            user["login_error"] = "auth.json not found"
            return self._save_metadata(user_id, user)

        env = {**os.environ, "CODEX_HOME": str(self._user_dir(user_id))}
        try:
            result = subprocess.run(
                [*_codex_command(), "login", "status"],
                cwd=str(BASE_DIR),
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )
        except Exception as exc:
            user["status"] = "error"
            user["login_error"] = str(exc)
            return self._save_metadata(user_id, user)

        output = "\n".join(part for part in [result.stdout, result.stderr] if part).strip()
        user["last_status_checked_at"] = _now()
        user["login_output"] = output
        user["login_error"] = "" if result.returncode == 0 else output
        user["status"] = "normal" if result.returncode == 0 else "error"
        user["usage"] = self._usage_for_user(user_id, output)
        return self._save_metadata(user_id, user)

    def update_user(self, user_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        user_id = _safe_user_id(user_id)
        user = self.get_user(user_id)
        if user is None:
            raise FileNotFoundError("codex user not found")
        for key in ("name", "enabled"):
            if key in updates:
                user[key] = updates[key]
        return self._save_metadata(user_id, user)

    def delete_user(self, user_id: str) -> bool:
        user_id = _safe_user_id(user_id)
        path = self._user_dir(user_id).resolve()
        root = CODEX_USERS_DIR.resolve()
        if root not in path.parents:
            raise ValueError("invalid codex user path")
        if not path.exists():
            return False
        with self._lock:
            process = self._login_processes.pop(user_id, None)
        if process is not None and process.poll() is None:
            process.terminate()
        shutil.rmtree(path)
        if self.get_default_user_id() == user_id:
            remaining = self.list_users()
            if remaining:
                self.set_default_user(str(remaining[0]["id"]))
            elif CODEX_DEFAULT_FILE.exists():
                CODEX_DEFAULT_FILE.unlink()
        return True

    def set_default_user(self, user_id: str) -> dict[str, Any]:
        user_id = _safe_user_id(user_id)
        user = self.get_user(user_id)
        if user is None:
            raise FileNotFoundError("codex user not found")
        _write_json(CODEX_DEFAULT_FILE, {"user_id": user_id, "updated_at": _now()})
        return self._load_metadata(user_id)

    def login_state(self, user_id: str) -> dict[str, Any]:
        user_id = _safe_user_id(user_id)
        user = self.get_user(user_id)
        if user is None:
            raise FileNotFoundError("codex user not found")
        with self._lock:
            process = self._login_processes.get(user_id)
        user["login_running"] = bool(process and process.poll() is None)
        return user

    def run_exec(
        self,
        prompt: str,
        user_id: str | None = None,
        cwd: str | None = None,
        model: str | None = None,
        sandbox: str = "workspace-write",
        timeout_secs: int = 1800,
    ) -> dict[str, Any]:
        selected_user_id = _safe_user_id(user_id or self.get_default_user_id())
        user = self.get_user(selected_user_id)
        if user is None:
            raise FileNotFoundError("codex user not found")
        if not user.get("enabled", True):
            raise ValueError("codex user is disabled")
        if not self._auth_path(selected_user_id).exists():
            raise ValueError("auth.json not found")

        workdir = Path(cwd).expanduser().resolve() if cwd else BASE_DIR.resolve()
        if not workdir.exists() or not workdir.is_dir():
            raise ValueError("working directory does not exist")
        prompt = str(prompt or "").strip()
        if not prompt:
            raise ValueError("prompt is required")

        allowed_sandbox = {"read-only", "workspace-write", "danger-full-access"}
        sandbox = sandbox if sandbox in allowed_sandbox else "workspace-write"
        command = [
            *_codex_command(),
            "exec",
            "--json",
            "--cd",
            str(workdir),
            "--ask-for-approval",
            "never",
            "--sandbox",
            sandbox,
        ]
        if model:
            command.extend(["--model", str(model).strip()])
        command.append(prompt)

        env = {**os.environ, "CODEX_HOME": str(self._user_dir(selected_user_id))}
        started_at = _now()
        try:
            result = subprocess.run(
                command,
                cwd=str(workdir),
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=max(1, int(timeout_secs or 1800)),
                check=False,
            )
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            result = None
            timed_out = True
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        if result is not None:
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            return_code = result.returncode
        else:
            return_code = -1

        user["last_used_at"] = _now()
        user["status"] = "normal" if return_code == 0 and not timed_out else "error"
        self._save_metadata(selected_user_id, user)
        return {
            "user_id": selected_user_id,
            "started_at": started_at,
            "finished_at": _now(),
            "return_code": return_code,
            "timed_out": timed_out,
            "stdout": stdout,
            "stderr": stderr,
        }


codex_service = CodexService()
