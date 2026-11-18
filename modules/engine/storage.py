"""modules/engine/storage.py - Gestión de persistencia SQLite en cluster.db."""

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from shared.exceptions import DatabaseError
from shared.models import AnalysisResult, BenchmarkComparison, JobRun, RunType
from shared.utils import calculate_speedup

DEFAULT_DB_NAME = "cluster.db"
DEFAULT_MOCK_DB_NAME = "cluster_mock.db"

# Raíz del proyecto (donde reside cluster.db en desarrollo)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _user_data_dir() -> Path:
    """Directorio de datos por usuario (XDG) para cuando la raíz del proyecto no es escribible."""
    base = os.getenv("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "cluster-ai"


def _anchor(path_value: str) -> Path:
    """
    Resuelve una ruta de base de datos. Las rutas relativas se anclan a la raíz del proyecto
    y NUNCA al directorio de trabajo actual (que, bajo sudo, es la carpeta del investigador).
    """
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def resolve_db_path(db_path: Optional[str] = None, force_mock: bool = False) -> Path:
    """
    Determina la ruta de la base de datos según el modo de ejecución.
    Si force_mock=True o CLUSTER_AI_MODE=mock, aísla los datos en cluster_mock.db.
    """
    if db_path:
        return _anchor(str(db_path))

    env_mode = os.getenv("CLUSTER_AI_MODE", "cluster").strip().lower()
    is_mock = force_mock or (env_mode == "mock")

    if is_mock:
        chosen = os.getenv("CLUSTER_AI_MOCK_DB_PATH")
        if not chosen:
            # En el servidor la raíz del proyecto pertenece a root: la base mock de cada
            # investigador va a su directorio de datos personal.
            if os.access(PROJECT_ROOT, os.W_OK):
                return _anchor(DEFAULT_MOCK_DB_NAME)
            data_dir = _user_data_dir()
            data_dir.mkdir(parents=True, exist_ok=True)
            return (data_dir / DEFAULT_MOCK_DB_NAME).resolve()
    else:
        chosen = os.getenv("CLUSTER_AI_DB_PATH", DEFAULT_DB_NAME)

    return _anchor(chosen)

class StorageEngine:
    def __init__(self, db_path: Optional[str] = None, force_mock: bool = False):
        self.db_path = resolve_db_path(db_path, force_mock=force_mock)
        self.is_mock = force_mock or ("mock" in self.db_path.name.lower())
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=20.0)
        conn.row_factory = sqlite3.Row
        # Activar modo WAL para alta concurrencia
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_db(self) -> None:
        """Inicializa el esquema de tablas si no existen."""
        with self._get_connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                job_id INTEGER PRIMARY KEY,
                user TEXT NOT NULL,
                script_name TEXT NOT NULL,
                script_hash TEXT NOT NULL,
                run_type TEXT NOT NULL,
                partition TEXT NOT NULL,
                nodes INTEGER NOT NULL,
                cores INTEGER NOT NULL,
                walltime_seconds REAL NOT NULL,
                cpu_time_seconds REAL NOT NULL,
                cpu_efficiency_pct REAL NOT NULL,
                max_rss_bytes INTEGER NOT NULL,
                core_hours REAL NOT NULL,
                exit_code INTEGER DEFAULT 0,
                timestamp TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_runs_script_hash ON runs(script_hash);
            CREATE INDEX IF NOT EXISTS idx_runs_run_type ON runs(run_type);

            CREATE TABLE IF NOT EXISTS analysis_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                script_path TEXT NOT NULL,
                script_hash TEXT NOT NULL,
                backup_path TEXT,
                optimized_script_path TEXT,
                recommended_partition TEXT,
                report_markdown_path TEXT,
                report_content TEXT,
                timestamp TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS code_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER,
                file_path TEXT NOT NULL,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                explanation TEXT NOT NULL,
                original_code TEXT NOT NULL,
                optimized_code TEXT NOT NULL,
                estimated_gain TEXT,
                FOREIGN KEY (report_id) REFERENCES analysis_reports(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS mock_jobs (
                job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_name TEXT NOT NULL,
                user TEXT NOT NULL,
                partition TEXT NOT NULL,
                status TEXT NOT NULL,
                nodes INTEGER DEFAULT 1,
                cores INTEGER DEFAULT 1,
                time_limit TEXT,
                submit_time TEXT NOT NULL,
                start_time TEXT,
                end_time TEXT,
                script_path TEXT,
                script_content TEXT,
                output_file TEXT,
                error_file TEXT,
                exit_code INTEGER DEFAULT 0,
                elapsed_seconds REAL DEFAULT 0.0,
                max_rss_bytes INTEGER DEFAULT 0,
                cpu_efficiency_pct REAL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS ai_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user TEXT NOT NULL,
                action TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_ai_usage_user_ts ON ai_usage(user, timestamp);
            """)
            # Migración: bases previas no registraban el autor de cada análisis
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(analysis_reports)")}
            if "user" not in columns:
                conn.execute("ALTER TABLE analysis_reports ADD COLUMN user TEXT")

    def save_run(self, run: JobRun) -> int:
        """Guarda o actualiza la telemetría de una corrida."""
        query = """
        INSERT INTO runs (
            job_id, user, script_name, script_hash, run_type, partition,
            nodes, cores, walltime_seconds, cpu_time_seconds, cpu_efficiency_pct,
            max_rss_bytes, core_hours, exit_code, timestamp
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_id) DO UPDATE SET
            user=excluded.user,
            run_type=excluded.run_type,
            walltime_seconds=excluded.walltime_seconds,
            cpu_time_seconds=excluded.cpu_time_seconds,
            cpu_efficiency_pct=excluded.cpu_efficiency_pct,
            max_rss_bytes=excluded.max_rss_bytes,
            core_hours=excluded.core_hours,
            exit_code=excluded.exit_code,
            timestamp=excluded.timestamp;
        """
        with self._get_connection() as conn:
            conn.execute(query, (
                run.job_id, run.user, run.script_name, run.script_hash, run.run_type.value,
                run.partition, run.nodes, run.cores, run.walltime_seconds, run.cpu_time_seconds,
                run.cpu_efficiency_pct, run.max_rss_bytes, run.core_hours, run.exit_code, run.timestamp
            ))
        return run.job_id

    def get_run(self, job_id: int) -> Optional[JobRun]:
        """Recupera la telemetría de una corrida por su job_id."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM runs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                return None
            return JobRun(
                job_id=row["job_id"],
                user=row["user"],
                script_name=row["script_name"],
                script_hash=row["script_hash"],
                run_type=RunType(row["run_type"]),
                partition=row["partition"],
                nodes=row["nodes"],
                cores=row["cores"],
                walltime_seconds=row["walltime_seconds"],
                cpu_time_seconds=row["cpu_time_seconds"],
                cpu_efficiency_pct=row["cpu_efficiency_pct"],
                max_rss_bytes=row["max_rss_bytes"],
                core_hours=row["core_hours"],
                exit_code=row["exit_code"],
                timestamp=row["timestamp"]
            )

    def list_runs(self, limit: int = 50, user: Optional[str] = None) -> List[JobRun]:
        """Lista el historial de corridas registradas."""
        query = "SELECT * FROM runs"
        params = []
        if user:
            query += " WHERE user = ?"
            params.append(user)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [
                JobRun(
                    job_id=r["job_id"],
                    user=r["user"],
                    script_name=r["script_name"],
                    script_hash=r["script_hash"],
                    run_type=RunType(r["run_type"]),
                    partition=r["partition"],
                    nodes=r["nodes"],
                    cores=r["cores"],
                    walltime_seconds=r["walltime_seconds"],
                    cpu_time_seconds=r["cpu_time_seconds"],
                    cpu_efficiency_pct=r["cpu_efficiency_pct"],
                    max_rss_bytes=r["max_rss_bytes"],
                    core_hours=r["core_hours"],
                    exit_code=r["exit_code"],
                    timestamp=r["timestamp"]
                ) for r in rows
            ]

    def save_analysis(self, result: AnalysisResult, markdown_content: str = "", user: Optional[str] = None) -> int:
        """Guarda un informe de diagnóstico, su autor y sus recomendaciones asociadas."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
            INSERT INTO analysis_reports (
                script_path, script_hash, backup_path, optimized_script_path,
                recommended_partition, report_markdown_path, report_content, timestamp, user
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                result.script_path, result.script_hash, result.backup_path,
                result.optimized_script_path, result.recommended_partition,
                result.report_markdown_path, markdown_content, result.timestamp, user
            ))
            report_id = cursor.lastrowid
            for sug in result.code_suggestions:
                conn.execute("""
                INSERT INTO code_suggestions (
                    report_id, file_path, category, title, explanation,
                    original_code, optimized_code, estimated_gain
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    report_id, sug.file_path, sug.category, sug.title,
                    sug.explanation, sug.original_code, sug.optimized_code, sug.estimated_gain
                ))
            return report_id

    def record_ai_usage(self, user: str, action: str) -> None:
        """Registra una consulta al proveedor de IA (para límites de uso y auditoría)."""
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO ai_usage (user, action, timestamp) VALUES (?, ?, ?)",
                (user, action, datetime.now().isoformat()),
            )

    def count_ai_usage_since(self, user: str, since: datetime) -> int:
        """Cuenta las consultas de IA de un usuario desde un instante dado."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM ai_usage WHERE user = ? AND timestamp >= ?",
                (user, since.isoformat()),
            ).fetchone()
            return int(row["n"])

    def compare_runs(self, baseline_id: int, optimized_id: int) -> BenchmarkComparison:
        """Calcula la comparativa empírica entre corrida baseline y optimizada."""
        base = self.get_run(baseline_id)
        opt = self.get_run(optimized_id)
        if not base or not opt:
            raise DatabaseError(f"No se pudieron encontrar ambos trabajos para comparar ({baseline_id}, {optimized_id})")

        speedup = calculate_speedup(base.walltime_seconds, opt.walltime_seconds)
        time_saved = max(0.0, base.walltime_seconds - opt.walltime_seconds)
        eff_gain = round(opt.cpu_efficiency_pct - base.cpu_efficiency_pct, 2)
        mem_saved = max(0, base.max_rss_bytes - opt.max_rss_bytes)
        core_hours_saved = max(0.0, base.core_hours - opt.core_hours)

        return BenchmarkComparison(
            baseline_run=base,
            optimized_run=opt,
            speedup=speedup,
            walltime_saved_seconds=time_saved,
            cpu_efficiency_gain_pct=eff_gain,
            memory_saved_bytes=mem_saved,
            core_hours_saved=round(core_hours_saved, 4)
        )
