"""从 AG-UI 官方 Git 仓库同步文档源文件和配套资源。"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


REPOSITORY = "https://github.com/ag-ui-protocol/ag-ui.git"
BRANCH = "main"
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "docs" / "references" / "ag-ui"
DOCS_DIR = OUTPUT_DIR / "official-docs"


def run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="agent-service-agui-") as temp_dir:
        repo_dir = Path(temp_dir) / "ag-ui"
        run_git(
            "clone",
            "--depth",
            "1",
            "--filter=blob:none",
            "--sparse",
            "--branch",
            BRANCH,
            REPOSITORY,
            str(repo_dir),
        )
        run_git("-C", str(repo_dir), "sparse-checkout", "set", "docs")
        revision = run_git("-C", str(repo_dir), "rev-parse", "HEAD")
        source_docs = repo_dir / "docs"
        shutil.copytree(source_docs, DOCS_DIR, dirs_exist_ok=True)
        shutil.copy2(repo_dir / "LICENSE", OUTPUT_DIR / "LICENSE")

    files = sorted(path for path in DOCS_DIR.rglob("*") if path.is_file())
    pages = [path for path in files if path.suffix.lower() in {".md", ".mdx"}]
    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest = {
        "source_repository": REPOSITORY,
        "source_site": "https://docs.ag-ui.com",
        "site_index": "https://docs.ag-ui.com/llms.txt",
        "branch": BRANCH,
        "revision": revision,
        "license": "MIT",
        "retrieved_at": retrieved_at,
        "file_count": len(files),
        "page_count": len(pages),
        "files": [
            {
                "path": path.relative_to(DOCS_DIR).as_posix(),
                "bytes": path.stat().st_size,
            }
            for path in files
        ],
    }
    (OUTPUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    readme = f"""# AG-UI 官方文档镜像

本目录保存 AG-UI 官方仓库 docs/ 下的完整文档源码和资源，包含 {len(pages)} 篇 Markdown/MDX 页面、共 {len(files)} 个文件。同步时间：{retrieved_at}。

- 官方文档站：<https://docs.ag-ui.com>
- 官方站页面清单：<https://docs.ag-ui.com/llms.txt>
- 官方源码：<https://github.com/ag-ui-protocol/ag-ui>
- 本地文档和图片：official-docs/
- 抓取版本、文件清单和大小：manifest.json
- MIT 许可：LICENSE

### SSE 接入相关页面

- 服务端快速开始：official-docs/quickstart/server.mdx
- SSE 传输规范：official-docs/spec/1.0/basic/transports/http-sse.mdx
- 事件总览：official-docs/spec/1.0/events/index.mdx
- 运行输入：official-docs/spec/1.0/basic/run-input.mdx

页面、导航配置和配套资源按上游文件保留；完整协议文本以 official-docs/spec/1.0/ 为准，草案放在 official-docs/spec/draft/。

在 agent-service 根目录重复同步最新版：

    python scripts/sync_agui_docs.py
"""
    (OUTPUT_DIR / "README.md").write_text(readme, encoding="utf-8", newline="\n")
    print(
        f"已同步 AG-UI {revision[:12]}：{len(pages)} 篇页面、"
        f"{len(files)} 个文件，保存到 {DOCS_DIR}"
    )


if __name__ == "__main__":
    main()
