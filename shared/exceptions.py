"""Excepciones de dominio y operativas para Cluster-AI."""

class ClusterAIError(Exception):
    """Excepción base del sistema Cluster-AI."""
    pass

class SlurmCommandError(ClusterAIError):
    """Error al invocar o parsear comandos del sistema Slurm."""
    pass

class AIClientError(ClusterAIError):
    """Error de comunicación o cuota con el proveedor de IA (protocolo compatible con OpenAI)."""
    pass

# Retrocompatibilidad con AgnesAIError
AgnesAIError = AIClientError

class LinterValidationError(ClusterAIError):
    """Fallo en la validación determinística de scripts."""
    pass

class BackupError(ClusterAIError):
    """Error al guardar archivos generados conservando su versión anterior."""
    pass

class ConfigurationError(ClusterAIError):
    """Error en los archivos de configuración YAML o variables de entorno."""
    pass

class DatabaseError(ClusterAIError):
    """Error en la base de datos cluster.db."""
    pass
