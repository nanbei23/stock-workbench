"""
数据快照捕获 — 通过 LangChain callback 拦截七层分析师的工具调用，
把工具返回的原始数据存入 task._data_snapshot[stage_id]。

不修改 tradingagents 库代码，纯外部拦截。
"""
import logging
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

logger = logging.getLogger(__name__)

# 工具名 → 所属分析师阶段的映射
# 同一个工具可能被多个阶段使用，需要结合当前运行阶段判断
TOOL_TO_STAGES = {
    "get_stock_data": ["market", "hot_money"],
    "get_indicators": ["market"],
    "get_news": ["social", "news", "policy", "hot_money", "lockup"],
    "get_global_news": ["news", "policy"],
    "get_fundamentals": ["fundamentals", "lockup"],
    "get_balance_sheet": ["fundamentals"],
    "get_cashflow": ["fundamentals"],
    "get_insider_transactions": ["hot_money", "lockup"],
}

# 分析师阶段的执行顺序（用于推断当前阶段）
ANALYST_ORDER = ["market", "social", "news", "fundamentals", "policy", "hot_money", "lockup"]


class DataSnapshotCallback(BaseCallbackHandler):
    """拦截工具调用，捕获原始数据作为数据快照。

    用法：
        callback = DataSnapshotCallback()
        graph = TradingAgentsGraph(..., callbacks=[callback])
        # 分析完成后
        snapshot = callback.get_snapshot()  # {stage_id: [tool_outputs]}
    """

    def __init__(self):
        super().__init__()
        # {stage_id: [{"tool": name, "input": args, "output": result_str}, ...]}
        self._snapshot: dict[str, list[dict]] = {}
        # 当前正在执行的分析师阶段（由外部设置）
        self._current_stage: str | None = None
        # 缓冲区：收集工具调用，等阶段完成时再归档
        self._buffer: list[dict] = []
        # 工具名 → 最近一次调用的输入（用于 on_tool_end 时关联）
        self._pending_tools: dict[str, dict] = {}

    def set_current_stage(self, stage_id: str):
        """外部调用：告知当前正在执行的分析师阶段。"""
        # 如果切换了阶段，先把缓冲区的数据归档到上一个阶段
        if self._current_stage and self._current_stage != stage_id and self._buffer:
            self._snapshot.setdefault(self._current_stage, []).extend(self._buffer)
            self._buffer = []
        self._current_stage = stage_id

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id=None,
        parent_run_id=None,
        tags=None,
        metadata=None,
        inputs=None,
        **kwargs,
    ):
        """工具开始执行时记录。"""
        tool_name = serialized.get("name", "unknown")
        self._pending_tools[str(run_id)] = {
            "tool": tool_name,
            "input": inputs or input_str,
        }

    def on_tool_end(
        self,
        output: str,
        *,
        run_id=None,
        parent_run_id=None,
        **kwargs,
    ):
        """工具执行完毕时捕获返回值。"""
        pending = self._pending_tools.pop(str(run_id), None)
        tool_name = pending["tool"] if pending else "unknown"
        tool_input = pending["input"] if pending else {}

        entry = {
            "tool": tool_name,
            "input": tool_input,
            "output": output[:5000],  # 限制单条最大5K字符
        }
        self._buffer.append(entry)
        logger.debug("数据快照捕获: %s (stage=%s, len=%d)",
                      tool_name, self._current_stage, len(output))

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id=None,
        parent_run_id=None,
        **kwargs,
    ):
        """工具执行出错时记录。"""
        self._pending_tools.pop(str(run_id), None)

    def flush(self):
        """将缓冲区数据归档到当前阶段。调用时机：某阶段完成时。"""
        if self._current_stage and self._buffer:
            self._snapshot.setdefault(self._current_stage, []).extend(self._buffer)
            self._buffer = []
            logger.info("数据快照 flush: stage=%s, 条目=%d",
                        self._current_stage, len(self._snapshot.get(self._current_stage, [])))

    def get_snapshot(self) -> dict[str, list[dict]]:
        """获取完整数据快照。先 flush 再返回。"""
        self.flush()
        return dict(self._snapshot)

    def get_stage_snapshot(self, stage_id: str) -> str:
        """获取指定阶段的数据快照文本（用于事实账本对比）。"""
        self.flush()
        entries = self._snapshot.get(stage_id, [])
        if not entries:
            return ""
        parts = []
        for e in entries:
            parts.append(f"[{e['tool']}]\n{e['output']}")
        return "\n\n".join(parts)

    def get_summary(self) -> dict[str, int]:
        """获取快照摘要：每个阶段有多少条工具调用。"""
        self.flush()
        return {k: len(v) for k, v in self._snapshot.items()}
