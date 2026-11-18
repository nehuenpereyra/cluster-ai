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


DEFAULT_HELPER_PATH = "/usr/local/libexec/cluster-ai-priv"
_PRODUCTION_VALUES = ("production", "produccion", "producción", "prod")
_DEVELOPMENT_VALUES = ("development", "desarrollo", "dev")


def is_production() -> bool:
    """
    Indica si el CLI corre en el servidor de producción.

    - `CLUSTER_AI_ENV=production`: producción (se ocultan el emulador y las opciones --mock).
    - `CLUSTER_AI_ENV=development`: desarrollo (todo visible).
    - Sin la variable: producción si está instalado el helper privilegiado del servidor
      (`/usr/local/libexec/cluster-ai-priv`), desarrollo en cualquier otro caso.
    """
    value = os.getenv("CLUSTER_AI_ENV", "").strip().lower()
    if value in _PRODUCTION_VALUES:
        return True
    if value in _DEVELOPMENT_VALUES:
        return False
    return os.name == "posix" and os.path.isfile(DEFAULT_HELPER_PATH)


def analysis_scope() -> str:
    """Devuelve el alcance del diagnóstico en modo local: 'sbatch' (por defecto) o 'full'."""
    return SCOPE_FULL if code_audit_enabled() else SCOPE_SBATCH
