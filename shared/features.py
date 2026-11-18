"""shared/features.py - Alcance funcional habilitado en la fase actual de Cluster-AI."""

import os

SCOPE_SBATCH = "sbatch"
SCOPE_FULL = "full"


def code_audit_enabled() -> bool:
    """
    Indica si está habilitada la auditoría del código fuente científico (proyecto completo).

    En la fase actual solo se optimiza el script #SBATCH. La auditoría de código queda
    deshabilitada por defecto y solo puede activarse en modo local de desarrollo con
    `CLUSTER_AI_ENABLE_CODE_AUDIT=true`. El helper privilegiado del servidor la ignora siempre.
    """
    return os.getenv("CLUSTER_AI_ENABLE_CODE_AUDIT", "false").strip().lower() in ("1", "true", "yes", "si", "sí")


def analysis_scope() -> str:
    """Devuelve el alcance del diagnóstico en modo local: 'sbatch' (por defecto) o 'full'."""
    return SCOPE_FULL if code_audit_enabled() else SCOPE_SBATCH
