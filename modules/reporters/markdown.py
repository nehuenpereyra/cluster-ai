"""modules/reporters/markdown.py - Generador de informes técnicos en Markdown para investigadores."""

from datetime import datetime
from pathlib import Path
from typing import List, Optional
from shared.features import SCOPE_FULL
from shared.models import AnalysisResult, CodeOptimizationSuggestion
from shared.utils import AUTHOR_INFO

class MarkdownReporter:
    """Compila y guarda un informe técnico exhaustivo en Markdown para el investigador."""

    def generate_report(
        self,
        result: AnalysisResult,
        ai_insights: str = "",
        sbatch_diff: str = "",
        output_file: Optional[Path] = None
    ) -> Path:
        script_path = Path(result.script_path)
        if not output_file:
            output_file = script_path.parent / f"{script_path.stem}_INFORME_MEJORA.md"

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        code_audit = result.scope == SCOPE_FULL

        lines: List[str] = []
        lines.append(f"# Informe de Diagnóstico y Optimización de Simulación")
        lines.append(f"**Proyecto / Script Evaluado:** `{script_path.name}`  ")
        lines.append(f"**Fecha:** {now_str}  ")
        lines.append(f"**Entorno de Ejecución:** Clúster HPC - Instituto de Astrofísica de La Plata (CONICET / UNLP)  ")
        lines.append(f"**Copia de Respaldo Inmutable:** `{result.backup_path or 'N/A'}`  ")
        lines.append(f"**Script Optimizado Sugerido:** `{result.optimized_script_path or 'N/A'}`  ")
        lines.append("\n---\n")

        # 1. Resumen Ejecutivo
        lines.append("## 1. Resumen Ejecutivo y Recomendaciones de Cola")
        lines.append(f"- **Cola Recomendada:** `{result.recommended_partition}` (según el tiempo de ejecución estimado y límites de partición del IALP).")
        lines.append(f"- **Integridad del Código Original:** El archivo original ha sido respaldado de manera inmutable antes de generar sugerencias.")
        lines.append(f"- **Problemas Detectados en Directivas:** {len(result.issues)} observaciones.")
        if code_audit:
            lines.append(f"- **Oportunidades de Mejora en Código:** {len(result.code_suggestions)} sugerencias algorítmicas.")
        else:
            lines.append("- **Alcance del Análisis:** directivas y recursos del script #SBATCH (la auditoría del código fuente no está habilitada en esta fase).")
        lines.append("\n")

        # 2. Diagnóstico de Directivas #SBATCH
        lines.append("## 2. Diagnóstico de Directivas Slurm (#SBATCH)")
        if result.issues:
            lines.append("| Regla | Severidad | Diagnóstico | Sugerencia |")
            lines.append("| :--- | :--- | :--- | :--- |")
            for iss in result.issues:
                lines.append(f"| `{iss.rule_id}` | **{iss.severity.value.upper()}** | {iss.message} | `{iss.suggestion or '-'}` |")
        else:
            lines.append("No se detectaron infracciones en las directivas de Slurm. La configuración actual cumple las políticas del IALP.")
        lines.append("\n")

        # Diff de sbatch
        if sbatch_diff:
            lines.append("### Comparativa de Directivas (Original vs Optimizado)")
            lines.append("```diff")
            lines.append(sbatch_diff.strip())
            lines.append("```\n")

        # 3. Auditoría del Código Fuente Científico
        lines.append("## 3. Auditoría y Optimización del Código Fuente Científico")
        if not code_audit:
            lines.append("La auditoría del código fuente científico (proyecto completo) no está habilitada en esta fase de Cluster-AI. "
                         "Este informe se limita a la optimización del script de envío #SBATCH.")
        elif result.code_suggestions:
            for idx, sug in enumerate(result.code_suggestions, start=1):
                lines.append(f"### 3.{idx}. {sug.title} (`{sug.category}`)")
                lines.append(f"**Archivo:** `{sug.file_path}`  ")
                lines.append(f"**Impacto Estimado:** {sug.estimated_gain or 'Significativo'}  ")
                lines.append(f"\n{sug.explanation}\n")
                lines.append("#### Código Original:")
                lines.append(f"```python\n{sug.original_code}\n```")
                lines.append("#### Código Optimizado Propuesto:")
                lines.append(f"```python\n{sug.optimized_code}\n```\n")
        else:
            lines.append("No se detectaron patrones ineficientes críticos en el análisis estático inicial del código fuente.")
        lines.append("\n")

        # 4. Análisis Profundo del Asistente IA
        if ai_insights:
            lines.append("## 4. Análisis Avanzado del Asistente IA")
            lines.append(ai_insights)
            lines.append("\n")

        # 5. Recomendaciones de Buenas Prácticas en el IALP
        lines.append("## 5. Buenas Prácticas Operativas en el Clúster IALP")
        lines.append("- **Topología de Nodos:** node001-node002 disponen de 16 núcleos (Ryzen 5950X, 32 CPUs en Slurm) y node003-node005 de 12 núcleos (Ryzen 5900X, 24 CPUs). Para cómputo intensivo, usar como máximo un proceso por núcleo físico.")
        lines.append("- **Almacenamiento Scratch:** Para lecturas y escrituras intensivas de simulación, utilizar los 500GB SSD locales de cada nodo (`/scratch`), reservando el disco magnético de 8TB para conjuntos de datos definitivos.")
        lines.append("- **Corridas Cortas:** Utilice el comando `cluster-ai short-run <script.sh>` para validar el consumo de memoria y CPU antes de enviar trabajos extensos a las colas `medium` o `long`.")
        lines.append("\n---\n")

        # Firma del autor al pie del informe
        lines.append(f"*{AUTHOR_INFO}*  \n")
        lines.append(f"*Generado automáticamente por Cluster-AI - Soporte HPC IALP*")

        content = "\n".join(lines)
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(content)

        return output_file
