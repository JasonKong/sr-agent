"""Where the project is, independent of the working directory the server is started in.

Claude Code and Claude Desktop do not promise a working directory for stdio servers, so
`.env` and relative paths such as `var/logs` are resolved against the project root:
`CLAUDE_PROJECT_DIR` when it is set, otherwise the uv workspace this package belongs to.
"""

from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    for parent in Path(__file__).resolve().parents:
        pyproject = parent / "pyproject.toml"
        if pyproject.is_file() and "[tool.uv.workspace]" in pyproject.read_text():
            return parent
    return Path.cwd()


def resolve(path: Path, root: Path) -> Path:
    return path if path.is_absolute() else root / path
