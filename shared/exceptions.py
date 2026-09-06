"""Excepciones de dominio y operativas para Cluster-AI."""

class ClusterAIError(Exception):
    """Excepción base del sistema Cluster-AI."""
    pass

class SlurmCommandError(ClusterAIError):
    """Error al invocar o parsear comandos del sistema Slurm."""
    pass

class AgnesAIError(ClusterAIError):
    """Error de comunicación o cuota con la API de Agnes AI."""
    pass

class LinterValidationError(ClusterAIError):
    """Fallo en la validación determinística de scripts."""
    pass

class BackupError(ClusterAIError):
    """Error al crear o verificar copias de respaldo de archivos."""
    pass

class ConfigurationError(ClusterAIError):
    """Error en los archivos de configuración YAML o variables de entorno."""
    pass

class DatabaseError(ClusterAIError):
    """Error en la base de datos cluster.db."""
    pass
