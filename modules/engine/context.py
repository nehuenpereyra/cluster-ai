"""modules/engine/context.py - Ensamblador de contexto híbrido (Hardware IALP + Reglas XML + Código Fuente)."""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from shared.filesystem import safe_read_file

class ContextAssembler:
    """
    Ensambla el contexto dinámico y curado para enviar a Agnes AI.
    Combina especificaciones físicas del clúster IALP, reglas prácticas en XML,
    el script sbatch y el código fuente científico que se ejecuta.
    """

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or Path(__file__).resolve().parent.parent.parent
        self.config_path = self.base_dir / "config" / "cluster.yaml"
        self.rules_dir = self.base_dir / "knowledge" / "rules"

    def _load_cluster_summary(self) -> str:
        if not self.config_path.is_file():
            return "Clúster HPC IALP (5 nodos, AMD Ryzen 9 5950X/5900X, colas: short, medium, long, prolonged)."
        with open(self.config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return yaml.dump(data, allow_unicode=True, default_flow_style=False)

    def _load_rules(self) -> str:
        rules_text = []
        if self.rules_dir.is_dir():
            for xml_file in self.rules_dir.glob("*.xml"):
                try:
                    rules_text.append(f"<!-- Reglas de {xml_file.name} -->\n" + safe_read_file(xml_file))
                except Exception:
                    pass
        return "\n\n".join(rules_text)

    def find_invoked_code_files(self, sbatch_script_path: Path, sbatch_content: str) -> List[Path]:
        """Detecta scripts fuente (Python, C, Fortran, etc.) invocados dentro del sbatch."""
        invoked_files: List[Path] = []
        work_dir = sbatch_script_path.parent

        # Buscar llamadas a scripts: python [script.py], ./[script], bash [script.sh]
        matches = re.findall(r"(?:python\d?|srun\s+python\d?|bash|sh|\.\/)\s+([^\s;&|]+\.(?:py|cpp|c|f90|f|sh))", sbatch_content)
        for m in matches:
            candidate = work_dir / m
            if candidate.is_file() and candidate not in invoked_files:
                invoked_files.append(candidate)

        # Si no encontró ninguno por regex directo, buscar archivos .py en el mismo directorio
        if not invoked_files:
            for py_file in work_dir.glob("*.py"):
                if py_file.is_file() and py_file.name != "main.py":
                    invoked_files.append(py_file)
                    break

        return invoked_files

    def assemble_ai_prompt(
        self,
        sbatch_path: Path,
        sbatch_content: str,
        issues_summary: str = "",
        historical_telemetry: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, str]]:
        """Construye los mensajes para el modelo agnes-2.5-flash."""
        cluster_info = self._load_cluster_summary()
        rules_info = self._load_rules()
        invoked_files = self.find_invoked_code_files(sbatch_path, sbatch_content)

        code_sections = []
        for code_file in invoked_files:
            try:
                code_content = safe_read_file(code_file)
                # Limitar tamaño de archivo para no saturar contexto
                if len(code_content) > 12000:
                    code_content = code_content[:12000] + "\n... [Código truncado para análisis] ..."
                code_sections.append(f"### Archivo de Código Fuente: `{code_file.name}`\n```python\n{code_content}\n```")
            except Exception:
                pass

        invoked_code_text = "\n\n".join(code_sections) if code_sections else "No se adjuntó archivo de código fuente adicional."

        system_message = (
            "Eres el asistente inteligente de optimización HPC de Cluster-AI para el Instituto de Astrofísica de La Plata (IALP - CONICET / UNLP).\n"
            "Tu objetivo es auditar scripts de envío Slurm (#SBATCH) y el código fuente científico (Python, C, MPI, etc.) ejecutado por los investigadores.\n"
            "Debes proveer recomendaciones concretas de mejora de eficiencia, reducción de tiempo de ejecución y uso óptimo de recursos, "
            "teniendo en cuenta la arquitectura exacta de hardware (AMD Ryzen 9 5950X / 5900X Zen 3, memoria 128GB, red 2.5GbE) y las políticas de colas (short, medium, long, prolonged).\n"
            "Usa un tono técnico, riguroso pero claro, en idioma español."
        )

        user_content = f"""
Por favor audita y optimiza el siguiente trabajo del clúster IALP.

## Especificaciones del Clúster IALP:
```yaml
{cluster_info}
```

## Reglas de Conocimiento Curadas:
{rules_info}

## Script #SBATCH Original (`{sbatch_path.name}`):
```bash
{sbatch_content}
```

## Código Fuente Asociado a la Simulación:
{invoked_code_text}

## Problemas Detectados por Linters Determinísticos:
{issues_summary or "Ningún error crítico de sintaxis detectado."}

## Telemetría Previa:
{historical_telemetry or "No hay corridas previas registradas para este script."}

Genera un informe estructurado en formato Markdown que contenga:
1. **Resumen de Diagnóstico de Recursos**: Adecuación de la cola elegida, cores y memoria solicitada.
2. **Mejoras en las Directivas #SBATCH**: Ajustes necesarios y justificación para el clúster IALP.
3. **Auditoría Profunda del Código Fuente**: Cuellos de botella detectados en el código de simulación (vectorización, uso de CPU/GIL, E/S de almacenamiento, paralelismo) con bloques de código comparativos (Antes vs Después).
4. **Impacto Estimado**: Aceleración (Speedup) proyectada y ahorro de horas-núcleo.
"""

        return [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_content}
        ]
