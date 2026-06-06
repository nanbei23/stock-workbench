import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from models.database import SCHEMA
from scripts import batch_research


class BatchResearchScriptTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(SCHEMA)
            conn.executemany(
                "INSERT INTO watchlist (code, name, group_name, sort_order) VALUES (?, ?, ?, ?)",
                [
                    ("000001", "平安银行", "默认", 1),
                    ("600519", "贵州茅台", "默认", 2),
                    ("000063", "中兴通讯", "观察池", 3),
                ],
            )
            conn.execute(
                "INSERT INTO analysis_reports (code, task_id, signal, created_at) VALUES (?, ?, ?, datetime('now'))",
                ("600519", "old-task", "HOLD"),
            )
            conn.commit()

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_candidates_filters_group_and_recent_reports(self):
        candidates = batch_research.load_candidates(self.db_path, group="默认", skip_recent_days=7)

        self.assertEqual([item.code for item in candidates], ["000001"])

    def test_rank_candidates_prioritizes_self_selected_and_positive_change(self):
        stocks = [
            batch_research.StockCandidate("000001", "平安银行", "默认", 1),
            batch_research.StockCandidate("000063", "中兴通讯", "观察池", 2),
        ]
        quotes = {
            "000001": {"price": 10.0, "change_pct": 1.2, "amount": 900000000},
            "000063": {"price": 20.0, "change_pct": 5.0, "amount": 2000000000},
        }

        ranked = batch_research.rank_candidates(stocks, quotes, top_n=2)

        self.assertEqual(ranked[0].code, "000001")
        self.assertGreater(ranked[0].score, ranked[1].score)

    def test_sina_report_list_payload_renders_financial_statement(self):
        payload = {
            "result": {
                "data": {
                    "report_date": [
                        {"date_value": "20260331", "date_description": "2026一季报"},
                    ],
                    "report_list": {
                        "20260331": {
                            "rType": "合并期末",
                            "rCurrency": "CNY",
                            "data": [
                                {"item_title": "货币资金", "item_value": "123456789.120000"},
                                {"item_title": "流动资产合计", "item_value": "987654321.000000"},
                            ],
                        }
                    },
                }
            }
        }

        rendered = batch_research._format_sina_financial_report(
            "600699",
            "资产负债表",
            "quarterly",
            payload,
            source="sina direct HTTP",
            retrieved_at="2026-06-03 18:00:00",
        )

        self.assertIn("# Balance Sheet for 600699", rendered)
        self.assertIn("report_date,report_name,rType,rCurrency,item_title,item_value", rendered)
        self.assertIn("20260331,2026一季报,合并期末,CNY,货币资金,123456789.120000", rendered)

    def test_eastmoney_financial_payload_renders_fallback_statement(self):
        rows = [
            {
                "SECUCODE": "600699.SH",
                "SECURITY_CODE": "600699",
                "SECURITY_NAME_ABBR": "均胜电子",
                "REPORT_DATE": "2026-03-31 00:00:00",
                "NOTICE_DATE": "2026-04-28 00:00:00",
                "MONETARYFUNDS": 9002435463.1,
                "TOTAL_ASSETS": 69154537276.32,
                "TOTAL_LIABILITIES": 45127676766.85,
            }
        ]

        rendered = batch_research._format_eastmoney_financial_report(
            "600699",
            "资产负债表",
            rows,
            curr_date="2026-06-03",
            retrieved_at="2026-06-03 18:10:00",
        )

        self.assertIn("# Balance Sheet for 600699", rendered)
        self.assertIn("# Data source: eastmoney datacenter fallback", rendered)
        self.assertIn("report_date,notice_date,security_name,item_field,item_value", rendered)
        self.assertIn("2026-03-31 00:00:00,2026-04-28 00:00:00,均胜电子,MONETARYFUNDS,9002435463.1", rendered)

    def test_snapshot_prompt_includes_investment_profile_context(self):
        snapshot_row = {
            "id": 7,
            "snapshot": {"market": {"quote": {"price": 10.0}}},
            "validation": {"ok": True, "missing_layers": [], "empty_layers": [], "layer_errors": {}},
            "created_at": "2026-06-04 10:00:00",
            "summary": {},
        }
        stock = batch_research.RankedCandidate("000001", "平安银行", "默认", 1, 0.0, {"price": 10.0})
        state = batch_research._initial_snapshot_agent_state(stock, snapshot_row)

        prompt = batch_research._snapshot_tradingagents_state_prompt(
            stock,
            snapshot_row,
            role_key="portfolio_manager",
            role_name="Portfolio Manager",
            role_goal="最终裁决",
            output_key="final_trade_decision",
            state=state,
            investment_profile_context="用户投资风格：进攻型。允许右侧突破后提高仓位，单票上限 40%。",
        )

        self.assertIn("用户投资风格：进攻型", prompt)
        self.assertIn("单票上限 40%", prompt)
        self.assertIn("strategy_checklist", prompt)
        self.assertIn("最终裁决必须输出 JSON 对象", prompt)

    def test_snapshot_prompts_include_holding_context(self):
        snapshot_row = {
            "id": 7,
            "snapshot": {"market": {"quote": {"price": 25.5}}},
            "validation": {"ok": True, "missing_layers": [], "empty_layers": [], "layer_errors": {}},
            "created_at": "2026-06-04 10:00:00",
            "summary": {},
        }
        stock = batch_research.RankedCandidate("002241", "歌尔股份", "默认", 1, 0.0, {"price": 25.5})
        holding_context = {
            "is_holding": True,
            "prompt_context": "## 当前账户持仓上下文\n- 真实持仓: 1000.000 股\n- 持仓成本: 26.006\n",
        }

        prompt = batch_research._snapshot_prompt(stock, snapshot_row, holding_context=holding_context)
        debate_prompt = batch_research._snapshot_debate_prompt(
            stock,
            snapshot_row,
            role_name="交易员/最终裁决",
            role_goal="最终裁决",
            previous_discussion=[],
            holding_context=holding_context,
        )
        state_prompt = batch_research._snapshot_tradingagents_state_prompt(
            stock,
            snapshot_row,
            role_key="portfolio_manager",
            role_name="Portfolio Manager",
            role_goal="最终裁决",
            output_key="final_trade_decision",
            state=batch_research._initial_snapshot_agent_state(stock, snapshot_row, holding_context=holding_context),
            holding_context=holding_context,
        )

        self.assertIn("当前账户持仓上下文", prompt)
        self.assertIn("research_signal", prompt)
        self.assertIn("account_signal", debate_prompt)
        self.assertIn("position_action", state_prompt)

    def test_snapshot_llm_config_resolves_model_library_reference(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO model_providers
                    (id, name, base_url, api_key, models_json, quick_model, deep_model, default_model, context_length, usage_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "provider-ai",
                    "Provider AI",
                    "https://provider.example.com/v1",
                    "sk-provider",
                    json.dumps(["provider-fast-ref", "provider-deep-ref"], ensure_ascii=False),
                    "provider-fast-ref",
                    "provider-deep-ref",
                    "provider-deep-ref",
                    "128000",
                    json.dumps(["ai"], ensure_ascii=False),
                ),
            )
            conn.executemany(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                [
                    ("ai_primary_provider_id", "provider-ai"),
                    ("ai_quick_model", "stale-fast-ref"),
                    ("ai_deep_model", "stale-deep-ref"),
                    ("custom_endpoint", "https://legacy.example.com/v1"),
                    ("api_key", "sk-legacy"),
                    ("quick_think_model", "legacy-fast"),
                    ("deep_think_model", "legacy-deep"),
                ],
            )
            conn.commit()

        deep = batch_research._snapshot_llm_config(self.db_path, model_tier="deep")
        quick = batch_research._snapshot_llm_config(self.db_path, model_tier="quick")

        self.assertEqual(deep["base_url"], "https://provider.example.com/v1")
        self.assertEqual(deep["api_key"], "sk-provider")
        self.assertEqual(deep["model"], "provider-deep-ref")
        self.assertEqual(quick["model"], "provider-fast-ref")

    def test_verification_llm_config_resolves_model_library_reference(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO model_providers
                    (id, name, base_url, api_key, models_json, quick_model, deep_model, default_model, usage_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "provider-verify",
                    "Provider Verify",
                    "https://verify.example.com/v1",
                    "sk-verify",
                    json.dumps(["provider-verify-model"], ensure_ascii=False),
                    "",
                    "provider-verify-model",
                    "provider-verify-model",
                    json.dumps(["verification"], ensure_ascii=False),
                ),
            )
            conn.executemany(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                [
                    ("verification_provider_id", "provider-verify"),
                    ("verification_model", "stale-verify-model"),
                    ("verification_endpoint", "https://legacy-verify.example.com/v1"),
                    ("verification_api_key", "sk-legacy-verify"),
                ],
            )
            conn.commit()

        config = batch_research._verification_llm_config(self.db_path)

        self.assertEqual(config["base_url"], "https://verify.example.com/v1")
        self.assertEqual(config["api_key"], "sk-verify")
        self.assertEqual(config["model"], "provider-verify-model")

    def test_save_snapshot_report_persists_account_signal_and_holding_context(self):
        snapshot_row = {
            "id": 8,
            "snapshot": {"market": {"quote": {"price": 25.5}}},
            "validation": {"ok": True, "missing_layers": [], "empty_layers": [], "layer_errors": {}},
            "created_at": "2026-06-04 10:00:00",
            "summary": {"price": 25.5},
        }
        stock = batch_research.RankedCandidate("002241", "歌尔股份", "默认", 1, 0.0, {"price": 25.5})
        holding_context = {
            "is_holding": True,
            "shares": 1000,
            "avg_cost": 26.006,
            "prompt_context": "## 当前账户持仓上下文\n- 真实持仓: 1000.000 股\n",
        }

        report_id = batch_research._save_snapshot_report(
            self.db_path,
            stock,
            {
                "research_signal": "BUY",
                "account_signal": "HOLD",
                "position_action": "hold",
                "action_reason": "研究偏多，但已有持仓且未回到成本线。",
                "confidence": 0.66,
                "risk_score": 42,
                "final_decision": "账户维持持有。",
            },
            snapshot_row,
            run_id="unit",
            duration_seconds=1.2,
            model="unit-model",
            holding_context=holding_context,
        )

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT signal, raw_state FROM analysis_reports WHERE id = ?", (report_id,)).fetchone()

        self.assertEqual(row["signal"], "HOLD")
        raw_state = json.loads(row["raw_state"])
        self.assertEqual(raw_state["research_signal"], "BUY")
        self.assertEqual(raw_state["account_signal"], "HOLD")
        self.assertEqual(raw_state["position_action"], "hold")
        self.assertEqual(raw_state["holding_context"]["avg_cost"], 26.006)

    def test_validate_snapshot_flags_semantic_financial_failures(self):
        snapshot = {
            "market": {"quote": {"ok": True, "payload": {"price": 10}}},
            "social": {"news": {"ok": True, "payload": "ok"}},
            "news": {"stock_news": {"ok": True, "payload": "ok"}},
            "fundamentals": {
                "fundamentals": {"ok": True, "payload": "估值数据"},
                "balance_sheet": {"ok": True, "payload": "No balance sheet data found for A-stock '600699'"},
                "cashflow": {"ok": True, "payload": "Error retrieving cash flow for 600699: timeout"},
            },
            "policy": {"global_news": {"ok": True, "payload": "ok"}},
            "hot_money": {"news": {"ok": True, "payload": "ok"}},
            "lockup": {"fundamentals": {"ok": True, "payload": "ok"}},
        }

        validation = batch_research.validate_snapshot(snapshot)

        self.assertFalse(validation["ok"])
        self.assertIn("fundamentals", validation["layer_errors"])
        joined = "\n".join(validation["layer_errors"]["fundamentals"])
        self.assertIn("balance_sheet", joined)
        self.assertIn("cashflow", joined)

    def test_resolve_role_model_configs_uses_private_provider_key(self):
        provider_payload = [
            {
                "id": "third",
                "name": "第三方引擎",
                "base_url": "https://third.example.com/v1",
                "api_key": "sk-private",
                "models": ["fast-model", "deep-model"],
            }
        ]
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                ("model_providers", json.dumps(provider_payload, ensure_ascii=False)),
            )
            conn.commit()

        resolved = batch_research._resolve_role_model_configs(
            self.db_path,
            {"risk_manager": {"provider_id": "third", "model": "deep-model"}},
        )

        self.assertEqual(resolved["risk_manager"]["base_url"], "https://third.example.com/v1")
        self.assertEqual(resolved["risk_manager"]["api_key"], "sk-private")
        self.assertEqual(resolved["risk_manager"]["model"], "deep-model")
        self.assertEqual(resolved["risk_manager"]["_profile"], "第三方引擎")

    async def test_dry_run_builds_plan_without_submitting_ai_tasks(self):
        with patch("scripts.batch_research.get_batch_quotes", new=AsyncMock(return_value={"000001": {"price": 10.0, "change_pct": 1.0}})), patch(
            "services.ai_analysis_service.start_analysis",
            new=AsyncMock(),
        ) as start_analysis:
            result = await batch_research.run_batch_research(
                db_path=self.db_path,
                group="默认",
                include_observation=False,
                limit=5,
                top_n=1,
                batch_size=1,
                data_only=False,
                dry_run=True,
                skip_recent_days=7,
                output_dir=Path(self.tmp.name),
            )

        self.assertEqual(result["planned_count"], 1)
        self.assertEqual(result["submitted_count"], 0)
        start_analysis.assert_not_awaited()

    async def test_data_only_prewarms_quotes_without_submitting_ai_tasks(self):
        snapshot = {
            "market": {"quote": {"price": 10.0}},
            "social": {"items": ["ok"]},
            "news": {"items": ["ok"]},
            "fundamentals": {"items": ["ok"]},
            "policy": {"items": ["ok"]},
            "hot_money": {"items": ["ok"]},
            "lockup": {"items": ["ok"]},
        }
        with patch("scripts.batch_research.get_batch_quotes", new=AsyncMock(return_value={"000001": {"price": 10.0, "change_pct": 1.0}})), patch(
            "scripts.batch_research.fetch_seven_layer_snapshot",
            new=AsyncMock(return_value=snapshot),
        ) as fetch_snapshot, patch(
            "services.ai_analysis_service.start_analysis",
            new=AsyncMock(),
        ) as start_analysis:
            result = await batch_research.run_batch_research(
                db_path=self.db_path,
                group="默认",
                include_observation=False,
                limit=5,
                top_n=1,
                batch_size=1,
                data_only=True,
                dry_run=False,
                skip_recent_days=7,
                output_dir=Path(self.tmp.name),
            )

        self.assertEqual(result["mode"], "data_only")
        self.assertEqual(result["snapshots"]["saved"], 1)
        self.assertEqual(result["submitted_count"], 0)
        fetch_snapshot.assert_awaited_once()
        start_analysis.assert_not_awaited()

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT code, snapshot_json, validation_json FROM stock_data_snapshots").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "000001")
        self.assertEqual(json.loads(rows[0][1])["market"]["quote"]["price"], 10.0)
        self.assertTrue(json.loads(rows[0][2])["ok"])

    async def test_fetch_seven_layer_snapshot_uses_fixed_sina_financial_reports(self):
        stock = batch_research.StockCandidate("600699", "均胜电子", "默认", 1)

        async def fake_invoke_tool(_tool, payload):
            if "curr_date" in payload or "start_date" in payload or "ticker" in payload:
                return {"ok": True, "payload": "tool ok"}
            return {"ok": True, "payload": "ok"}

        async def fake_financial(code, report_type, **_kwargs):
            return {"ok": True, "payload": f"{code} {report_type} fixed"}

        with patch("data.helpers.tencent_quote_batch", new=AsyncMock(return_value={"600699": {"price": 26.93}})), patch(
            "scripts.batch_research._invoke_tool",
            new=AsyncMock(side_effect=fake_invoke_tool),
        ), patch(
            "scripts.batch_research._invoke_sina_financial_report",
            new=AsyncMock(side_effect=fake_financial),
        ) as financial:
            snapshot = await batch_research.fetch_seven_layer_snapshot(stock, trade_date="2026-06-03")

        self.assertEqual(financial.await_count, 2)
        self.assertEqual(financial.await_args_list[0].args[:2], ("600699", "资产负债表"))
        self.assertEqual(financial.await_args_list[1].args[:2], ("600699", "现金流量表"))
        self.assertEqual(snapshot["fundamentals"]["balance_sheet"]["payload"], "600699 资产负债表 fixed")
        self.assertEqual(snapshot["fundamentals"]["cashflow"]["payload"], "600699 现金流量表 fixed")

    async def test_fetch_seven_layer_snapshot_flags_missing_quote_as_market_error(self):
        stock = batch_research.StockCandidate("603342", "无效代码", "默认", 1)

        async def fake_invoke_tool(_tool, payload):
            return {"ok": True, "payload": "tool ok"}

        async def fake_financial(code, report_type, **_kwargs):
            return {"ok": True, "payload": f"{code} {report_type} fixed"}

        with patch("data.helpers.tencent_quote_batch", new=AsyncMock(return_value={})), patch(
            "scripts.batch_research._invoke_tool",
            new=AsyncMock(side_effect=fake_invoke_tool),
        ), patch(
            "scripts.batch_research._invoke_sina_financial_report",
            new=AsyncMock(side_effect=fake_financial),
        ):
            snapshot = await batch_research.fetch_seven_layer_snapshot(stock, trade_date="2026-06-03")

        validation = batch_research.validate_snapshot(snapshot)

        self.assertFalse(validation["ok"])
        self.assertIn("market", validation["layer_errors"])
        self.assertIn("No quote data found", validation["layer_errors"]["market"][0])

    async def test_snapshot_analysis_uses_saved_snapshot_without_tradingagents(self):
        snapshot = {
            "market": {"quote": {"price": 10.0}},
            "social": {"items": ["ok"]},
            "news": {"items": ["ok"]},
            "fundamentals": {"items": ["ok"]},
            "policy": {"items": ["ok"]},
            "hot_money": {"items": ["ok"]},
            "lockup": {"items": ["ok"]},
        }
        validation = batch_research.validate_snapshot(snapshot)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO stock_data_snapshots
                    (code, name, snapshot_json, validation_json, summary_json, source, run_id)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "000001",
                    "平安银行",
                    json.dumps(snapshot, ensure_ascii=False),
                    json.dumps(validation, ensure_ascii=False),
                    "{}",
                    "test",
                    "run-1",
                ),
            )
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('custom_endpoint', 'https://api.example.com/v1')")
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('api_key', 'sk-test')")
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('deep_think_model', 'model-deep')")
            conn.commit()

        llm_result = {
            "signal": "BUY",
            "confidence": 0.76,
            "risk_score": 32.5,
            "market_report": "价格结构改善",
            "sentiment_report": "情绪中性",
            "news_report": "无重大负面",
            "fundamentals_report": "基本面稳定",
            "policy_report": "政策无明显冲击",
            "hot_money_report": "资金温和",
            "lockup_report": "解禁风险可控",
            "investment_debate": "多方略占优",
            "risk_debate": "控制仓位",
            "trader_plan": "分批建仓",
            "final_decision": "评级：BUY，置信度 76%，风险评分 32.5",
        }
        with patch("scripts.batch_research.get_batch_quotes", new=AsyncMock(return_value={"000001": {"price": 10.0, "change_pct": 1.0}})), patch(
            "scripts.batch_research._call_snapshot_llm",
            new=AsyncMock(return_value=llm_result),
        ) as call_llm, patch(
            "services.ai_analysis_service.start_analysis",
            new=AsyncMock(),
        ) as start_analysis:
            result = await batch_research.run_batch_research(
                db_path=self.db_path,
                group="默认",
                include_observation=False,
                limit=5,
                top_n=1,
                batch_size=1,
                data_only=False,
                dry_run=False,
                skip_recent_days=7,
                output_dir=Path(self.tmp.name),
                analysis_mode="snapshot",
            )

        self.assertEqual(result["mode"], "snapshot_analysis")
        self.assertEqual(result["submitted_count"], 1)
        call_llm.assert_awaited_once()
        start_analysis.assert_not_awaited()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT code, signal, depth, model_mode FROM analysis_reports WHERE code = ?", ("000001",)).fetchone()
        self.assertEqual(row[0], "000001")
        self.assertEqual(row[1], "BUY")
        self.assertEqual(row[2], "snapshot")
        self.assertEqual(row[3], "snapshot_report")

    async def test_snapshot_debate_uses_saved_snapshot_without_fetching_or_tradingagents(self):
        snapshot = {
            "market": {"quote": {"price": 10.0}},
            "social": {"items": ["ok"]},
            "news": {"items": ["ok"]},
            "fundamentals": {"items": ["ok"]},
            "policy": {"items": ["ok"]},
            "hot_money": {"items": ["ok"]},
            "lockup": {"items": ["ok"]},
        }
        validation = batch_research.validate_snapshot(snapshot)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO stock_data_snapshots
                    (code, name, snapshot_json, validation_json, summary_json, source, run_id)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "000001",
                    "平安银行",
                    json.dumps(snapshot, ensure_ascii=False),
                    json.dumps(validation, ensure_ascii=False),
                    "{}",
                    "test",
                    "run-1",
                ),
            )
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('custom_endpoint', 'https://api.example.com/v1')")
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('api_key', 'sk-test')")
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('deep_think_model', 'model-deep')")
            conn.commit()

        role_outputs = [
            "价格结构改善",
            "基本面稳定",
            "新闻情绪中性，政策无明显冲击，资金温和，解禁风险可控",
            "多方认为可分批建仓",
            "空方认为仍需控制回撤",
            "风控建议轻仓试错",
            json.dumps(
                {
                    "signal": "BUY",
                    "confidence": 0.74,
                    "risk_score": 38,
                    "trader_plan": "分批建仓，跌破支撑失效",
                    "final_decision": "BUY，置信度 0.74，风险评分 38",
                },
                ensure_ascii=False,
            ),
        ]
        with patch("scripts.batch_research.get_batch_quotes", new=AsyncMock(return_value={"000001": {"price": 10.0, "change_pct": 1.0}})), patch(
            "scripts.batch_research.fetch_seven_layer_snapshot",
            new=AsyncMock(return_value=snapshot),
        ) as fetch_snapshot, patch(
            "scripts.batch_research._call_snapshot_llm",
            new=AsyncMock(return_value={}),
        ) as call_single, patch(
            "scripts.batch_research._call_snapshot_debate_role_llm",
            new=AsyncMock(side_effect=role_outputs),
            create=True,
        ) as call_role, patch(
            "services.ai_analysis_service.start_analysis",
            new=AsyncMock(),
        ) as start_analysis:
            result = await batch_research.run_batch_research(
                db_path=self.db_path,
                group="默认",
                include_observation=False,
                limit=5,
                top_n=1,
                batch_size=1,
                data_only=False,
                dry_run=False,
                skip_recent_days=7,
                output_dir=Path(self.tmp.name),
                analysis_mode="snapshot-debate",
            )

        self.assertEqual(result["mode"], "snapshot-debate_analysis")
        self.assertEqual(result["submitted_count"], 1)
        fetch_snapshot.assert_not_awaited()
        call_single.assert_not_awaited()
        self.assertEqual(call_role.await_count, 7)
        start_analysis.assert_not_awaited()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT code, signal, confidence, risk_score, depth, model_mode, raw_state FROM analysis_reports WHERE code = ?",
                ("000001",),
            ).fetchone()
        self.assertEqual(row[0], "000001")
        self.assertEqual(row[1], "BUY")
        self.assertEqual(row[2], 0.74)
        self.assertEqual(row[3], 38)
        self.assertEqual(row[4], "snapshot_debate")
        self.assertEqual(row[5], "snapshot_debate")
        self.assertEqual(len(json.loads(row[6])["role_discussion"]), 7)

    async def test_snapshot_tradingagents_graph_preserves_debate_state_shape(self):
        stock = batch_research.RankedCandidate(
            code="000001",
            name="平安银行",
            group_name="默认",
            sort_order=1,
            quote={"price": 10.0},
            score=1.0,
        )
        snapshot = {
            "market": {"quote": {"price": 10.0}},
            "social": {"items": ["ok"]},
            "news": {"items": ["ok"]},
            "fundamentals": {"items": ["ok"]},
            "policy": {"items": ["ok"]},
            "hot_money": {"items": ["ok"]},
            "lockup": {"items": ["ok"]},
        }
        snapshot_row = {
            "id": 1,
            "code": "000001",
            "name": "平安银行",
            "snapshot": snapshot,
            "validation": batch_research.validate_snapshot(snapshot),
            "summary": {},
            "created_at": "2026-06-03T09:30:00",
        }
        outputs = [
            "市场报告",
            "情绪报告",
            "新闻报告",
            "基本面报告",
            "政策报告",
            "游资报告",
            "解禁报告",
            "质量门控通过",
            "多头观点",
            "空头观点",
            "研究经理计划",
            "交易员计划",
            "激进风控",
            "保守风控",
            "中性风控",
            json.dumps({"signal": "BUY", "confidence": 0.8, "risk_score": 30, "final_decision": "最终买入"}, ensure_ascii=False),
        ]

        with patch("scripts.batch_research._call_snapshot_tradingagents_role_llm", new=AsyncMock(side_effect=outputs)) as call_role:
            result = await batch_research._run_snapshot_tradingagents_graph(
                stock,
                snapshot_row,
                {"base_url": "https://api.example.com/v1", "api_key": "sk", "model": "m"},
                timeout_seconds=120,
            )

        self.assertEqual(call_role.await_count, 16)
        self.assertEqual([item["role_key"] for item in result["role_discussion"][:8]], [
            "market_analyst",
            "social_analyst",
            "news_analyst",
            "fundamentals_analyst",
            "policy_analyst",
            "hot_money_analyst",
            "lockup_analyst",
            "quality_gate",
        ])
        state = result["snapshot_tradingagents_state"]
        self.assertEqual(state["investment_debate_state"]["count"], 2)
        self.assertEqual(state["investment_debate_state"]["current_response"], "研究经理计划")
        self.assertIn("Bear Analyst:", state["investment_debate_state"]["history"])
        self.assertEqual(state["risk_debate_state"]["count"], 3)
        self.assertEqual(state["risk_debate_state"]["latest_speaker"], "Judge")
        self.assertEqual(result["market_report"], "市场报告")
        self.assertEqual(result["trader_plan"], "交易员计划")
        self.assertEqual(result["signal"], "BUY")

    async def test_recent_reports_are_skipped_for_next_batch_but_kept_in_position_plan(self):
        snapshot = {
            "market": {"quote": {"price": 10.0}},
            "social": {"items": ["ok"]},
            "news": {"items": ["ok"]},
            "fundamentals": {"items": ["ok"]},
            "policy": {"items": ["ok"]},
            "hot_money": {"items": ["ok"]},
            "lockup": {"items": ["ok"]},
        }
        validation = batch_research.validate_snapshot(snapshot)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO stock_data_snapshots
                    (code, name, snapshot_json, validation_json, summary_json, source, run_id)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "000001",
                    "平安银行",
                    json.dumps(snapshot, ensure_ascii=False),
                    json.dumps(validation, ensure_ascii=False),
                    "{}",
                    "test",
                    "run-1",
                ),
            )
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('custom_endpoint', 'https://api.example.com/v1')")
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('api_key', 'sk-test')")
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('deep_think_model', 'model-deep')")
            conn.execute("UPDATE analysis_reports SET signal = 'BUY', confidence = 0.7, risk_score = 35 WHERE code = '600519'")
            conn.commit()

        llm_result = {
            "signal": "BUY",
            "confidence": 0.8,
            "risk_score": 28,
            "final_decision": "评级：BUY",
            "trader_plan": "分批建仓",
        }
        with patch(
            "scripts.batch_research.get_batch_quotes",
            new=AsyncMock(
                return_value={
                    "000001": {"price": 10.0, "change_pct": 1.0},
                    "600519": {"price": 1500.0, "change_pct": 0.5},
                }
            ),
        ), patch("scripts.batch_research._call_snapshot_llm", new=AsyncMock(return_value=llm_result)):
            result = await batch_research.run_batch_research(
                db_path=self.db_path,
                group="默认",
                include_observation=False,
                limit=0,
                top_n=0,
                batch_size=1,
                data_only=False,
                dry_run=False,
                skip_recent_days=7,
                output_dir=Path(self.tmp.name),
                analysis_mode="snapshot",
            )

        self.assertEqual(result["planned_count"], 1)
        self.assertEqual(result["candidates"][0]["code"], "000001")
        self.assertEqual(result["skipped_existing_reports"], 1)
        self.assertEqual(result["position_plan"]["available_reports"], 2)
        self.assertIn("600519", {item["code"] for item in result["position_plan"]["recommendations"]})

    def test_snapshot_validation_marks_missing_layers(self):
        validation = batch_research.validate_snapshot({"market": {"quote": {"price": 10}}})

        self.assertFalse(validation["ok"])
        self.assertIn("social", validation["missing_layers"])

    def test_build_position_plan_uses_latest_reports_and_cash(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('cash_balance_default', '253375.680')")
            conn.execute(
                """
                INSERT INTO portfolio
                    (code, name, total_shares, available_shares, avg_cost, current_price, market_value, unrealized_pnl, unrealized_pnl_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("000001", "平安银行", 1000, 1000, 10.000, 11.000, 11000.000, 1000.000, 10.000),
            )
            conn.execute(
                """
                INSERT INTO analysis_reports
                    (code, task_id, signal, confidence, risk_score, final_decision, trader_plan, created_at)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, datetime('now', '-1 minute'))
                """,
                ("000001", "report-buy", "BUY", 0.82, 24.5, "建议分批建仓", "回撤买入，目标 12.500",),
            )
            conn.execute(
                """
                INSERT INTO analysis_reports
                    (code, task_id, signal, confidence, risk_score, final_decision, trader_plan, created_at)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                ("000063", "report-hold", "HOLD", 0.62, 58.0, "等待确认", "暂不建仓",),
            )
            conn.commit()

        plan = batch_research.build_position_plan(
            self.db_path,
            [
                batch_research.StockCandidate("000001", "平安银行", "默认", 1),
                batch_research.StockCandidate("000063", "中兴通讯", "观察池", 2),
            ],
            top_n=5,
        )

        self.assertEqual(plan["cash"], 253375.68)
        self.assertEqual(plan["portfolio_context"]["position_count"], 1)
        self.assertEqual(plan["portfolio_context"]["total_assets"], 264375.68)
        self.assertEqual(plan["available_reports"], 2)
        self.assertEqual(plan["recommendations"][0]["code"], "000001")
        self.assertEqual(plan["recommendations"][0]["current_position"]["shares"], 1000)
        self.assertEqual(plan["recommendations"][0]["current_position"]["unrealized_pnl"], 1000.0)
        self.assertEqual(plan["recommendations"][0]["current_position"]["unrealized_pnl_pct"], 10.0)
        self.assertLessEqual(plan["recommendations"][0]["suggested_amount"], 253375.68 * 0.15)
        self.assertGreater(plan["recommendations"][0]["suggested_amount"], 0)
        self.assertIn("首批比例", plan["notes"][1] + plan["notes"][2])
        self.assertEqual(plan["recommendations"][1]["action"], "watch")

    def test_build_position_plan_keeps_zero_market_value_holding_as_valid_position(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('cash_balance_default', '100000')")
            conn.execute(
                """
                INSERT INTO portfolio
                    (code, name, total_shares, available_shares, avg_cost, current_price, market_value, unrealized_pnl, unrealized_pnl_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("002156", "通富微电", 3100, 3100, 71.207, 0, 0, 0, 0),
            )
            conn.execute(
                """
                INSERT INTO analysis_reports
                    (code, task_id, signal, confidence, risk_score, final_decision, trader_plan, created_at)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                ("002156", "report-held", "HOLD", 0.62, 45.0, "持仓观察", "不加仓",),
            )
            conn.commit()

        plan = batch_research.build_position_plan(
            self.db_path,
            [batch_research.StockCandidate("002156", "通富微电", "默认", 1)],
            top_n=5,
        )

        position = plan["portfolio_context"]["positions"][0]
        self.assertEqual(plan["portfolio_context"]["position_count"], 1)
        self.assertEqual(position["market_value"], round(3100 * 71.207, 3))
        self.assertEqual(position["valuation_source"], "cost_fallback")
        self.assertEqual(plan["recommendations"][0]["current_position"]["shares"], 3100)
        self.assertEqual(plan["recommendations"][0]["current_position"]["market_value"], round(3100 * 71.207, 3))


if __name__ == "__main__":
    unittest.main()
