"""modules/engine/ai.py - Cliente de integración universal para el protocolo OpenAI."""

import os
from typing import Dict, List, Optional
import httpx
from shared.exceptions import AIClientError, AgnesAIError
from shared.features import SCOPE_FULL, SCOPE_SBATCH

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"


class OpenAICompatibleClient:
    """
    Cliente universal compatible con el protocolo OpenAI (/chat/completions).
    Lee únicamente las variables estándar: OPENAI_API_KEY, OPENAI_BASE_URL y OPENAI_MODEL.
    La verificación TLS está activa por defecto (protege la API Key de intercepciones);
    solo puede desactivarse explícitamente con OPENAI_TLS_VERIFY=false.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 45.0,
        verify_tls: Optional[bool] = None
    ):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.model = model or os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
        self.timeout = timeout
        if verify_tls is None:
            verify_tls = os.getenv("OPENAI_TLS_VERIFY", "true").strip().lower() not in ("0", "false", "no")
        self.verify_tls = verify_tls

    def _redact(self, text: str) -> str:
        """Elimina la API Key de cualquier texto que pueda llegar a la salida del usuario."""
        if self.api_key and len(self.api_key) >= 8:
            text = text.replace(self.api_key, "***")
        return text

    @property
    def is_local_endpoint(self) -> bool:
        """Determina si la base_url apunta a un entorno local (Ollama, LM Studio, etc.)."""
        lower_url = self.base_url.lower()
        return "localhost" in lower_url or "127.0.0.1" in lower_url or "0.0.0.0" in lower_url

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stream: bool = False,
        scope: str = SCOPE_FULL
    ) -> str:
        """
        Envía solicitud de chat completion a cualquier proveedor compatible con OpenAI.
        Estructura del protocolo:
          URL: {base_url}/chat/completions
          Header: Authorization: Bearer {api_key}
          Body: { model, messages, temperature, max_tokens, [stream] }
        """
        effective_key = self.api_key
        if not effective_key:
            if self.is_local_endpoint:
                effective_key = "local-no-key-required"
            else:
                return self._generate_heuristic_fallback(
                    messages,
                    reason="Falta OPENAI_API_KEY en .env",
                    scope=scope
                )

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {effective_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }

        try:
            with httpx.Client(timeout=self.timeout, verify=self.verify_tls) as client:
                response = client.post(url, headers=headers, json=payload)
                if response.status_code == 200:
                    data = response.json()
                    choices = data.get("choices", [])
                    if choices:
                        return choices[0].get("message", {}).get("content", "")
                    return ""
                elif response.status_code == 401:
                    return self._generate_heuristic_fallback(
                        messages,
                        reason=self._redact(f"401 Unauthorized desde {self.base_url} (modelo '{self.model}'). Verificar OPENAI_API_KEY en .env: {response.text[:120]}"),
                        scope=scope
                    )
                else:
                    return self._generate_heuristic_fallback(
                        messages,
                        reason=self._redact(f"HTTP {response.status_code} desde {self.base_url} (modelo '{self.model}'): {response.text[:120]}"),
                        scope=scope
                    )
        except Exception as exc:
            return self._generate_heuristic_fallback(
                messages,
                reason=self._redact(f"Fallo de conexión con el proveedor IA en {self.base_url} (modelo '{self.model}'): {exc}"),
                scope=scope
            )

    def _generate_heuristic_fallback(
        self,
        messages: List[Dict[str, str]],
        reason: str = "",
        scope: str = SCOPE_FULL
    ) -> str:
        """
        Generador de auditoría heurística determinística en modo contingencia (offline),
        garantizando que el investigador siempre reciba un reporte técnico de alta calidad
        incluso si el proveedor de IA externo no está disponible.
        Con scope='sbatch' las recomendaciones se limitan a directivas y recursos Slurm.
        """
        if scope == SCOPE_SBATCH:
            return self._sbatch_fallback(reason)
        return (
            f"> [!WARNING]\n"
            f"> **Modo Contingencia Heurístico Activado**: {reason}.\n"
            f"> El análisis a continuación ha sido generado mediante el motor de reglas estáticas y la base de conocimiento del clúster IALP.\n\n"
            f"### Auditoría y Recomendaciones Técnicas de Rendimiento (IALP HPC)\n\n"
            f"1. **Optimización de Vectorización y SIMD (Zen 3)**:\n"
            f"   - Los procesadores **AMD Ryzen 9 5950X y 5900X** del IALP cuentan con doble unidad de ejecución AVX2 por núcleo. "
            f"Cualquier operación numérica en bucles iterativos puros de Python debe reemplazarse por arrays contiguos de NumPy o compilarse con `@numba.njit(fastmath=True)`.\n\n"
            f"2. **Gestión de Hilos y Arquitectura NUMA**:\n"
            f"   - El modelo Ryzen 5950X agrupa sus 16 núcleos en 2 Core Complex Dies (CCDs) de 8 núcleos cada uno con caché L3 compartida de 32MB. "
            f"Para simulaciones multiproceso, fijar afinidad de procesos mediante `srun --cpu-bind=cores` para evitar saltos entre CCDs y penalizaciones de latencia de memoria.\n\n"
            f"3. **Evasión del Global Interpreter Lock (GIL)**:\n"
            f"   - Si la tarea es CPU-bound en Python, asegurar el uso de `concurrent.futures.ProcessPoolExecutor` o `mpi4py`. El uso de `threading.Thread` limitará la ejecución a un único hilo de cómputo efectivo.\n\n"
            f"4. **Estrategia de Almacenamiento SSD vs HDD**:\n"
            f"   - Utilizar el almacenamiento SSD local de 500GB en el nodo (`/scratch`) para archivos temporales, checkpoints intermedios y tablas frecuentes, reservando el disco HDD de 8TB para archivo definitivo de resultados."
        )


    @staticmethod
    def _sbatch_fallback(reason: str) -> str:
        """Recomendaciones heurísticas limitadas a directivas #SBATCH y uso de recursos."""
        return (
            f"> [!WARNING]\n"
            f"> **Modo Contingencia Heurístico Activado**: {reason}.\n"
            f"> El análisis a continuación ha sido generado mediante el motor de reglas estáticas y la base de conocimiento del clúster IALP.\n\n"
            f"### Recomendaciones de Directivas y Recursos (IALP HPC)\n\n"
            f"1. **Cores por nodo (Zen 3)**:\n"
            f"   - Los nodos AMD Ryzen 9 5950X tienen 16 núcleos y los 5900X, 12. No solicitar más cores por nodo que los físicos disponibles; "
            f"ajustar `--ntasks`/`--cpus-per-task` al paralelismo real del programa.\n\n"
            f"2. **Afinidad de procesos y NUMA**:\n"
            f"   - El 5950X agrupa sus núcleos en 2 CCDs de 8 con caché L3 propia. Lanzar los procesos con `srun --cpu-bind=cores` "
            f"y exportar `OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK` si el programa usa hilos.\n\n"
            f"3. **Tiempo límite y cola**:\n"
            f"   - Declarar `--time` explícito y elegir la cola más corta que lo contenga (short, medium, long, prolonged). "
            f"Usar `cluster-ai short-run` para medir antes de enviar trabajos largos.\n\n"
            f"4. **Almacenamiento**:\n"
            f"   - Dirigir archivos temporales y checkpoints al SSD local del nodo (`/scratch`) y declarar `--output`/`--error` con `%j` "
            f"para no sobrescribir registros entre corridas."
        )


# Alias para compatibilidad con código existente
AIClient = OpenAICompatibleClient
AgnesAIClient = OpenAICompatibleClient
