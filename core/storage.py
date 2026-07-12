import sqlite3
import json
import os
from datetime import datetime

DB_FILE = os.getenv("NMPL_DB_FILE", "nmpl_analytics.db")
EPHEMERAL_METRICS = os.getenv("NMPL_EPHEMERAL_METRICS", "true").lower() == "true"
METRICS_RETENTION = int(os.getenv("NMPL_METRICS_RETENTION", "200"))


def _timestamp_value() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class StorageManager:
    def __init__(self):
        self.db_file = DB_FILE

    def init_storage(self):
        with sqlite3.connect(self.db_file) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metrics_timeline (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME,
                    target TEXT,
                    summary TEXT,
                    status_flag TEXT,
                    latency_ms REAL,
                    loss_pct REAL,
                    jitter_ms REAL
                )
            """)
            
            if EPHEMERAL_METRICS:
                conn.execute("DROP TRIGGER IF EXISTS trim_metrics_timeline")
                conn.execute(f"""
                    CREATE TRIGGER trim_metrics_timeline
                    AFTER INSERT ON metrics_timeline
                    BEGIN
                        DELETE FROM metrics_timeline
                        WHERE id <= (
                            SELECT id FROM metrics_timeline
                            ORDER BY id DESC
                            LIMIT 1 OFFSET {METRICS_RETENTION}
                        );
                    END
                """)
            else:
                conn.execute("DROP TRIGGER IF EXISTS trim_metrics_timeline")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS network_incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME,
                    target TEXT,
                    structural_fault_summary TEXT,
                    bottleneck_hop INTEGER,
                    bottleneck_host TEXT,
                    bottleneck_loss_pct REAL,
                    raw_telemetry_json TEXT,
                    resolved_flag INTEGER DEFAULT 0,
                    resolved_at DATETIME,
                    last_updated_at DATETIME
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS active_targets (
                    target TEXT PRIMARY KEY,
                    registered_at DATETIME
                )
            """)
            
            existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(network_incidents)")}
            if "resolved_at" not in existing_cols:
                conn.execute("ALTER TABLE network_incidents ADD COLUMN resolved_at DATETIME")
            if "last_updated_at" not in existing_cols:
                conn.execute("ALTER TABLE network_incidents ADD COLUMN last_updated_at DATETIME")