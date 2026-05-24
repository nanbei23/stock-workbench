"""gbrain CLI client — search and save to knowledge base.

Wraps the ``gbrain`` Bun CLI for L3 knowledge integration:
- search: query the knowledge base
- put:    save content (analysis reports, notes)
"""

import json
import logging
import subprocess
import threading
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_GBRAIN_CLI = Path.home() / ".bun" / "bin" / "gbrain"


# ============================================================
# Sync primitives (safe for thread-pool / scheduler use)
# ============================================================

def gbrain_search_sync(query: str, timeout: int = 5) -> str:
    """Synchronous gbrain search — returns raw stdout or empty string."""
    try:
        if not _GBRAIN_CLI.exists():
            return ""
        result = subprocess.run(
            [str(_GBRAIN_CLI), "search", query],
            capture_output=True, text=True, timeout=timeout,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def gbrain_save_sync(slug: str, title: str, content: str, timeout: int = 10) -> bool:
    """Synchronous gbrain put — returns True on success."""
    try:
        if not _GBRAIN_CLI.exists():
            return False
        tmp_path = Path("/tmp") / f"gbrain_{slug.replace('/', '_')}.md"
        tmp_path.write_text(content, encoding="utf-8")
        result = subprocess.run(
            [str(_GBRAIN_CLI), "put", slug, "--title", title, "--file", str(tmp_path)],
            capture_output=True, text=True, timeout=timeout,
        )
        tmp_path.unlink(missing_ok=True)
        return result.returncode == 0
    except Exception:
        return False


# ============================================================
# Higher-level helpers
# ============================================================

def get_context(code: str, name: str) -> str:
    """Search gbrain for relevant knowledge about a stock before analysis."""
    result = gbrain_search_sync(f"{code} {name}")
    if not result:
        result = gbrain_search_sync(code)
    return result


def write_analysis_report(task) -> None:
    """Write L2 analysis summary to gbrain after completion (fire-and-forget).

    Spawns a daemon thread so the caller is not blocked.
    """
    if not task.result:
        return

    def _do_write():
        try:
            code = task.code
            name = task.name
            signal = task.result.get("signal", "HOLD")
            target_price = task.result.get("target_price")
            confidence = task.result.get("confidence")
            reasoning = (task.result.get("reasoning") or "")[:800]
            slug = f"analysis/{code}/{date.today().isoformat()}"
            title = f"{name}({code}) {signal} {date.today().isoformat()}"
            lines = [
                f"# {title}", "",
                f"- Signal: **{signal}**",
                f"- Target Price: {target_price}",
                f"- Confidence: {confidence}",
                f"- Elapsed: {task.elapsed}s", "",
                "## Reasoning", reasoning,
            ]
            risk_debate = task.result.get("risk_debate", {})
            if risk_debate.get("decision"):
                lines += ["", "## Risk Assessment", risk_debate["decision"][:500]]
            ok = gbrain_save_sync(slug, title, "\n".join(lines))
            logger.info("gbrain auto-write %s: %s", slug, "ok" if ok else "failed")
        except Exception as e:
            logger.warning("gbrain auto-write failed: %s", e)

    threading.Thread(target=_do_write, daemon=True).start()


# ============================================================
# Async wrappers for API endpoints
# ============================================================

async def api_search(q: str) -> dict:
    """Search gbrain (for use from async API endpoint)."""
    import asyncio
    try:
        result = await asyncio.to_thread(gbrain_search_sync, q, 5)
        return {
            "query": q,
            "results": result if result else [],
            "error": None,
        }
    except Exception as e:
        return {"results": [], "message": f"gbrain查询失败: {e}"}


async def api_save(slug: str, title: str, content: str) -> dict:
    """Save content to gbrain (for use from async API endpoint)."""
    import asyncio
    try:
        ok = await asyncio.to_thread(gbrain_save_sync, slug, title, content, 5)
        return {
            "status": "ok" if ok else "error",
            "slug": slug,
            "message": "" if ok else "gbrain save failed",
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
