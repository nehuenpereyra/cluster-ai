"""shared/models.py - Modelos de datos Pydantic de Cluster-AI."""

from datetime import datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field

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
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())

class BenchmarkComparison(BaseModel):
    baseline_run: JobRun
    optimized_run: JobRun
    speedup: float
    walltime_saved_seconds: float
    cpu_efficiency_gain_pct: float
    memory_saved_bytes: int
    core_hours_saved: float
