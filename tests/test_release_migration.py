import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SCRIPT = ROOT / "scripts" / "migrate_2_8_1_to_2_9.py"
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_macos_x86.sh"
BUILD_INSTALLER_SCRIPT = ROOT / "scripts" / "build_macos_x86_installer.sh"
AI_JS = ROOT / "static" / "js" / "ai.js"
AI_TEMPLATE = ROOT / "templates" / "ai.html"
BATCH_WORKER_SCRIPT = ROOT / "scripts" / "run_batch_worker.py"
WORKER_POOL_SCRIPT = ROOT / "scripts" / "run_batch_worker_pool.py"
SETTINGS_JS = ROOT / "static" / "js" / "settings.js"
SETTINGS_TEMPLATE = ROOT / "templates" / "settings.html"


def load_migration_module():
    spec = importlib.util.spec_from_file_location("migrate_2_8_1_to_2_9", MIGRATION_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ReleaseMigrationTests(unittest.TestCase):
    def test_deploy_plist_template_has_one_port_value(self):
        source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertNotIn("<string>$port</string>\n    <string>$port</string>", source)

    def test_deploy_script_runs_release_migration(self):
        source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("scripts/migrate_2_8_1_to_2_9.py", source)
        self.assertNotIn("from models.database import init_db; asyncio.run(init_db())", source)

    def test_deploy_script_installs_batch_worker_service(self):
        source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("worker_install_launchd.sh", source)
        self.assertIn("worker_start.sh", source)

    def test_installer_package_excludes_runtime_data(self):
        source = BUILD_INSTALLER_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("--exclude 'data/batch_research'", source)
        self.assertIn("--exclude 'logs'", source)

    def test_ai_batch_research_requires_selected_codes(self):
        js = AI_JS.read_text(encoding="utf-8")
        html = AI_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("const codes = getSelectedCodes();", js)
        self.assertIn("function getBatchResearchOptions()", js)
        self.assertIn("请先在左侧自选股列表勾选", js)
        self.assertIn("codes,", js)
        self.assertIn("allow_all: false", js)
        self.assertIn("analysis_depth: batchOptions.depth", js)
        self.assertIn("model_mode: batchOptions.modelMode", js)
        self.assertIn("snapshot_model_tier: batchOptions.modelTier", js)
        self.assertNotIn("group: 'all',\n            top_n: 0", js)
        self.assertNotIn("snapshot_model_tier: 'deep'", js)
        self.assertIn('id="batchDepthSelect"', html)
        self.assertIn('id="batchModelModeSelect"', html)
        self.assertIn("生成所选报告", html)

    def test_batch_worker_supports_model_provider_pool(self):
        content = BATCH_WORKER_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("--model-provider-ids", content)
        self.assertIn("--model-tier", content)
        self.assertIn("model_provider_ids=provider_ids", content)

    def test_worker_pool_has_config_script_and_settings_ui(self):
        script = WORKER_POOL_SCRIPT.read_text(encoding="utf-8")
        js = SETTINGS_JS.read_text(encoding="utf-8")
        html = SETTINGS_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("get_worker_pool_config", script)
        self.assertIn("subprocess.Popen", script)
        self.assertIn("asyncio.run(init_db())", script)
        self.assertNotIn("init_db_sync", script)
        self.assertIn("/worker-pool/config", js)
        self.assertIn("saveWorkerPoolConfig", js)
        self.assertIn("workerPoolList", html)

    def test_migration_script_upgrades_2_8_1_database(self):
        module = load_migration_module()
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workbench.db"
            with sqlite3.connect(db_path) as db:
                db.executescript(
                    """
                    CREATE TABLE schema_migrations (
                        version INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    INSERT INTO schema_migrations(version, name) VALUES (1, 'baseline_schema_tracking');
                    CREATE TABLE batch_jobs (
                        job_id TEXT PRIMARY KEY,
                        job_type TEXT NOT NULL,
                        name TEXT,
                        status TEXT NOT NULL DEFAULT 'pending',
                        total_count INTEGER DEFAULT 0,
                        completed_count INTEGER DEFAULT 0,
                        failed_count INTEGER DEFAULT 0,
                        skipped_count INTEGER DEFAULT 0,
                        waiting_count INTEGER DEFAULT 0,
                        running_count INTEGER DEFAULT 0,
                        current_code TEXT,
                        payload_json TEXT DEFAULT '{}',
                        result_json TEXT DEFAULT '{}',
                        error TEXT,
                        created_at TEXT DEFAULT (datetime('now')),
                        started_at TEXT,
                        completed_at TEXT,
                        updated_at TEXT DEFAULT (datetime('now'))
                    );
                    CREATE TABLE batch_job_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id TEXT NOT NULL,
                        code TEXT NOT NULL,
                        name TEXT,
                        status TEXT NOT NULL DEFAULT 'pending',
                        snapshot_id INTEGER,
                        report_id INTEGER,
                        task_id TEXT,
                        error TEXT,
                        retry_count INTEGER DEFAULT 0,
                        created_at TEXT DEFAULT (datetime('now')),
                        started_at TEXT,
                        completed_at TEXT,
                        updated_at TEXT DEFAULT (datetime('now'))
                    );
                    INSERT INTO batch_jobs(job_id, job_type, name, status)
                    VALUES ('old-running', 'report_generation', '旧运行任务', 'running');
                    """
                )
                db.commit()

            result = module.migrate_database(db_path, create_backup=True)

            self.assertTrue(Path(result["backup_path"]).exists())
            self.assertEqual(result["status"], "ok")
            with sqlite3.connect(db_path) as db:
                job_columns = {row[1] for row in db.execute("PRAGMA table_info(batch_jobs)")}
                item_columns = {row[1] for row in db.execute("PRAGMA table_info(batch_job_items)")}
                step_columns = {row[1] for row in db.execute("PRAGMA table_info(batch_job_item_steps)")}
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                status, error = db.execute("SELECT status, error FROM batch_jobs WHERE job_id='old-running'").fetchone()

            self.assertIn("lease_owner", job_columns)
            self.assertIn("runtime_json", job_columns)
            self.assertIn("error_type", item_columns)
            self.assertIn("model_config_json", step_columns)
            self.assertIn("position_plans", tables)
            self.assertEqual(status, "interrupted")
            self.assertIn("2.9", error)


if __name__ == "__main__":
    unittest.main()
