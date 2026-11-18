"""shared/models.py - Modelos de datos Pydantic de Cluster-AI."""

import re
from datetime import datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator

class RunType(str, Enum):
    BASELINE = "baseline"
    OPTIMIZED = "optimized"
    SHORT_RUN = "short_run"

class LintSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

class LintIssue(BaseModel):
    rule_id: str
    severity: LintSeverity
    message: str
    line: Optional[int] = None
    suggestion: Optional[str] = None

class CodeOptimizationSuggestion(BaseModel):
    file_path: str
    category: str  # ej. "NumPy Vectorization", "GIL Bypass", "I/O Optimization"
    title: str
    explanation: str
    original_code: str
    optimized_code: str
    estimated_gain: Optional[str] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None

class JobRun(BaseModel):
    job_id: int
    user: str = "investigador"
    script_name: str
    script_hash: str
    run_type: RunType = RunType.BASELINE
    partition: str = "medium"
    nodes: int = 1
    cores: int = 1
    walltime_seconds: float = 0.0
    cpu_time_seconds: float = 0.0
    cpu_efficiency_pct: float = 0.0
    max_rss_bytes: int = 0
    core_hours: float = 0.0
    exit_code: int = 0
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())

class AnalysisResult(BaseModel):
    script_path: str
    script_hash: str
    backup_path: Optional[str] = None
    optimized_script_path: Optional[str] = None
    issues: List[LintIssue] = Field(default_factory=list)
    code_suggestions: List[CodeOptimizationSuggestion] = Field(default_factory=list)
    recommended_partition: str = "medium"
    estimated_core_savings: Optional[float] = None
    report_markdown_path: Optional[str] = None
    # Alcance del diagnóstico: "sbatch" (solo directivas) o "full" (incluye código fuente)
    scope: str = "sbatch"
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())

class BenchmarkComparison(BaseModel):
    baseline_run: JobRun
    optimized_run: JobRun
    speedup: float
    walltime_saved_seconds: float
    cpu_efficiency_gain_pct: float
    memory_saved_bytes: int
    core_hours_saved: float


# ---------------------------------------------------------------------------
# Protocolo del helper privilegiado (cliente → cluster-ai-priv, JSON por stdin)
# Toda entrada es no confiable: se validan tamaños, formatos y campos permitidos.
# ---------------------------------------------------------------------------

MAX_SBATCH_BYTES = 64 * 1024
MAX_ISSUES_SUMMARY = 16 * 1024
MAX_REPORT_CONTENT = 128 * 1024
MAX_PATH_LENGTH = 4096
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._-]{0,63}$")


class SbatchReviewRequest(BaseModel):
    """Solicitud de revisión con IA de un script #SBATCH (único uso permitido de la API Key)."""
    model_config = ConfigDict(extra="forbid")

    script_name: str = Field(min_length=1, max_length=255)
    sbatch_content: str = Field(min_length=1, max_length=MAX_SBATCH_BYTES)
    issues_summary: str = Field(default="", max_length=MAX_ISSUES_SUMMARY)

    @field_validator("script_name")
    @classmethod
    def _plain_file_name(cls, value: str) -> str:
        if any(ch in value for ch in ("/", "\\", "\x00")) or value in (".", ".."):
            raise ValueError("script_name debe ser un nombre de archivo, no una ruta")
        return value


class AnalysisRecordRequest(BaseModel):
    """Registro de un análisis de diagnóstico ya generado por el cliente."""
    model_config = ConfigDict(extra="forbid")

    script_path: str = Field(min_length=1, max_length=MAX_PATH_LENGTH)
    script_hash: str
    backup_path: Optional[str] = Field(default=None, max_length=MAX_PATH_LENGTH)
    optimized_script_path: Optional[str] = Field(default=None, max_length=MAX_PATH_LENGTH)
    report_markdown_path: Optional[str] = Field(default=None, max_length=MAX_PATH_LENGTH)
    recommended_partition: str = Field(default="medium", max_length=64)
    report_content: str = Field(default="", max_length=MAX_REPORT_CONTENT)

    @field_validator("script_hash")
    @classmethod
    def _sha256(cls, value: str) -> str:
        if not _SHA256_RE.match(value):
            raise ValueError("script_hash debe ser un SHA-256 hexadecimal en minúsculas")
        return value


class HistoryRequest(BaseModel):
    """Consulta del historial de corridas registradas."""
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=20, ge=1, le=500)
    user: Optional[str] = None

    @field_validator("user")
    @classmethod
    def _valid_username(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not _USERNAME_RE.match(value):
            raise ValueError("nombre de usuario inválido")
        return value
