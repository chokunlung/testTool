from __future__ import annotations

import hashlib
import re
from pathlib import Path


def repo_metadata(repo_path: str) -> dict[str, str]:
    repo = Path(repo_path).expanduser().resolve()
    if not repo.is_dir():
        raise ValueError(f"Repo 路徑不存在：{repo}")
    name = repo.name
    project_id = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not project_id:
        digest = hashlib.sha256(str(repo).encode("utf-8")).hexdigest()[:8]
        project_id = f"repo-{digest}"
    return {"path": str(repo), "name": name, "project_id": project_id}


def choose_repo_directory() -> dict[str, str] | None:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update()
    try:
        selected = filedialog.askdirectory(
            parent=root,
            title="選擇 Spring Boot Repo",
            mustexist=True,
        )
    finally:
        root.destroy()
    return repo_metadata(selected) if selected else None
