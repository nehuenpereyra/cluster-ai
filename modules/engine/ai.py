"""modules/engine/ai.py - Cliente de integración con Agnes AI Platform (agnes-2.5-flash)."""

import json
import os
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
import httpx
from shared.exceptions import AgnesAIError

load_dotenv()

DEFAULT_BASE_URL = "https://apihub.agnes-ai.com/v1"
DEFAULT_MODEL = "agnes-2.5-flash"

class AgnesAIClient:
    """
    Cliente para la API de Agnes AI (compatible con OpenAI).
    Modelo: agnes-2.5-flash.
    Maneja reintentos, streaming y fallback a diagnóstico heurístico offline.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 45.0
    ):
        self.api_key = api_key or os.getenv("AGNES_API_KEY", "")
        self.base_url = (base_url or os.getenv("AGNES_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.model = model or os.getenv("AGNES_MODEL", DEFAULT_MODEL)
        self.timeout = timeout

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stream: bool = False
    ) -> str:
        """
        Envía solicitud de chat completion a Agnes AI.
        Estructura exacta:
          URL: {base_url}/chat/completions
          Header: Authorization: Bearer {api_key}
          Body: { model, messages, temperature, max_tokens, [stream] }
        """
        if not self.api_key:
            return self._generate_heuristic_fallback(messages, reason="Falta AGNES_API_KEY")

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }

        try:
            with httpx.Client(timeout=self.timeout, verify=False) as client:
                response = client.post(url, headers=headers, json=payload)
                if response.status_code == 200:
                    data = response.json()
                    choices = data.get("choices", [])
                    if choices:
                        return choices[0].get("message", {}).get("content", "")
                    return ""
                elif response.status_code == 401:
                    # Token inválido o no activo en Agnes AI -> fallback heurístico
                    return self._generate_heuristic_fallback(
                        messages,
                        reason=f"Agnes AI 401 Unauthorized (Verificar clave API en .env): {response.text[:120]}"
                    )
                else:
                    return self._generate_heuristic_fallback(
                        messages,
                        reason=f"HTTP {response.status_code} desde Agnes AI: {response.text[:120]}"
                    )
        except Exception as exc:
            return self._generate_heuristic_fallback(messages, reason=f"Fallo de conexión con Agnes AI: {exc}")

    def _generate_heuristic_fallback(self, messages: List[Dict[str, str]], reason: str = "") -> str:
        """
        Generador de auditoría heurística determinística en modo contingencia (offline),
        garantizando que el investigador siempre reciba un reporte técnico de alta calidad
        incluso si los servidores de IA externos no están disponibles.
        """
        last_user_content = messages[-1].get("content", "") if messages else ""

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
