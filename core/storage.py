import sqlite3
import json

DB_FILE = "nmpl_analytics.db"

class StorageManager:
    def __init__(self):
        self.db_file = DB_FILE

    def init_storage(self):
        with sqlite3.connect(self.db_file) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metrics_timeline (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    target TEXT,
                    summary TEXT,
                    status_flag TEXT,
                    latency_ms REAL,
                    loss_pct REAL,
                    jitter_ms REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS network_incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    target TEXT,
                    structural_fault_summary TEXT,
                    bottleneck_hop INTEGER,
                    bottleneck_host TEXT,
                    bottleneck_loss_pct REAL,
                    raw_telemetry_json TEXT,
                    resolved_flag INTEGER DEFAULT 0
                )
            """)

    def log_heartbeat(self, target, summary, status_flag, metrics):
        with sqlite3.connect(self.db_file) as conn:
            conn.execute("""
                INSERT INTO metrics_timeline
                (target, summary, status_flag, latency_ms, loss_pct, jitter_ms)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                target,
                summary,
                status_flag,
                metrics["latency_ms"],
                metrics["loss_pct"],
                metrics["jitter_ms"]
            ))

    def log_incident(self, target, incident_payload):
        bottleneck = incident_payload.get("bottleneck", {}) or {}
        with sqlite3.connect(self.db_file) as conn:
            conn.execute("""
                INSERT INTO network_incidents
                (target, structural_fault_summary, bottleneck_hop, bottleneck_host,
                 bottleneck_loss_pct, raw_telemetry_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                target,
                incident_payload.get("summary", ""),
                bottleneck.get("hop"),
                bottleneck.get("host"),
                bottleneck.get("loss"),
                json.dumps(incident_payload, indent=2)
            ))

    def get_incident(self, incident_id):
        with sqlite3.connect(self.db_file) as conn:
            return conn.execute("""
                SELECT id, timestamp, target, structural_fault_summary,
                       bottleneck_hop, bottleneck_host, bottleneck_loss_pct, raw_telemetry_json
                FROM network_incidents
                WHERE id = ?
            """, (incident_id,)).fetchone()

    def get_metrics_timeline(self, target=None, limit=100):
        with sqlite3.connect(self.db_file) as conn:
            if target:
                return conn.execute("""
                    SELECT id, timestamp, target, summary, status_flag, latency_ms, loss_pct, jitter_ms
                    FROM metrics_timeline
                    WHERE target = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (target, limit)).fetchall()

            return conn.execute("""
                SELECT id, timestamp, target, summary, status_flag, latency_ms, loss_pct, jitter_ms
                FROM metrics_timeline
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,)).fetchall()

    def get_all_incidents(self, limit=100):
        with sqlite3.connect(self.db_file) as conn:
            return conn.execute("""
                SELECT id, timestamp, target, structural_fault_summary,
                       bottleneck_hop, bottleneck_host, bottleneck_loss_pct, raw_telemetry_json
                FROM network_incidents
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,)).fetchall()