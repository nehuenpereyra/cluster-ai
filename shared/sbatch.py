"""
shared/sbatch.py - Parser fiel de scripts de envío Slurm (#SBATCH).

Reproduce cómo lee `sbatch` un script:
  - Solo cuentan las líneas que empiezan exactamente con `#SBATCH` (`##SBATCH` está desactivada).
  - `sbatch` deja de procesar directivas en la primera línea que no es comentario ni está vacía:
    las `#SBATCH` posteriores se IGNORAN.
  - Dentro de una línea, un `#` precedido de espacio inicia un comentario.
  - Opciones cortas (`-p short`, `-pshort`), largas (`--partition=short`, `--partition short`)
    y abreviaturas únicas de opciones largas (`--part short`), como acepta getopt_long.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

SHORT_WITH_VALUE = {
    "A": "account", "a": "array", "B": "extra-node-info", "b": "begin", "C": "constraint",
    "c": "cpus-per-task", "D": "chdir", "d": "dependency", "e": "error", "F": "nodefile",
    "G": "gpus", "i": "input", "J": "job-name", "L": "licenses", "M": "clusters",
    "m": "distribution", "N": "nodes", "n": "ntasks", "o": "output", "p": "partition",
    "q": "qos", "S": "core-spec", "t": "time", "w": "nodelist", "x": "exclude",
}
SHORT_FLAGS = {
    "H": "hold", "h": "help", "I": "immediate", "k": "no-kill", "O": "overcommit",
    "Q": "quiet", "s": "oversubscribe", "V": "version", "v": "verbose", "W": "wait",
}
LONG_WITH_VALUE = set(SHORT_WITH_VALUE.values()) | {
    "mem", "mem-per-cpu", "mem-per-gpu", "ntasks-per-node", "ntasks-per-core", "ntasks-per-socket",
    "gres", "gpus-per-node", "gpus-per-task", "cpus-per-gpu", "mail-type", "mail-user",
    "time-min", "export", "signal", "comment", "deadline", "delay-boot", "reservation",
    "wckey", "open-mode", "cpu-freq", "hint", "threads-per-core", "cores-per-socket",
    "sockets-per-node", "mincpus", "tmp", "nice", "priority", "switches", "gpu-bind",
    "cpu-bind", "mem-bind", "propagate", "network", "profile", "acctg-freq", "uid", "gid",
    "kill-on-invalid-dep", "batch", "cluster-constraint", "container", "exclusive",
}
LONG_FLAGS = set(SHORT_FLAGS.values()) | {
    "contiguous", "requeue", "no-requeue", "parsable", "spread-job", "test-only",
    "use-min-nodes", "ignore-pbs", "get-user-env", "usage", "reboot",
}
# --exclusive admite valor opcional (--exclusive[=user|mcs]); solo se toma con '='
OPTIONAL_VALUE = {"exclusive", "nice", "get-user-env", "propagate"}
KNOWN_LONG = LONG_WITH_VALUE | LONG_FLAGS


@dataclass
class Directive:
    """Una opción dentro de una línea #SBATCH."""
    line_index: int                 # índice (0-based) de la línea en el script
    name: str                       # nombre largo canónico (p. ej. 'partition') o el texto original si es desconocida
    value: Optional[str]
    span: Tuple[int, int]           # posición [inicio, fin) de la opción y su valor en la línea
    style: str                      # 'short', 'long=' o 'long'
    raw: str                        # texto original de la opción
    known: bool = True

    @property
    def line_number(self) -> int:
        return self.line_index + 1


@dataclass
class SbatchScript:
    lines: List[str]
    header_end: int                                  # índice de la primera línea de comandos (len(lines) si no hay)
    directives: List[Directive] = field(default_factory=list)   # efectivas (cabecera)
    ignored: List[Directive] = field(default_factory=list)      # después de comandos: Slurm las ignora
    command_lines: List[Tuple[int, str]] = field(default_factory=list)  # (índice, línea) sin comentarios

    def get(self, name: str) -> Optional[Directive]:
        """Última aparición efectiva de una opción (la última gana, como en sbatch)."""
        found = [d for d in self.directives if d.name == name]
        return found[-1] if found else None

    def value(self, name: str) -> Optional[str]:
        d = self.get(name)
        return d.value if d else None

    @property
    def last_directive_line(self) -> Optional[int]:
        idxs = [d.line_index for d in self.directives]
        return max(idxs) if idxs else None


def _resolve_long(name: str) -> Tuple[str, bool]:
    if name in KNOWN_LONG:
        return name, True
    candidates = [k for k in KNOWN_LONG if k.startswith(name)]
    if len(candidates) == 1:
        return candidates[0], True
    return name, False


def _options_region(line: str) -> Tuple[int, int]:
    """Devuelve el rango de la línea con opciones (tras '#SBATCH', antes del comentario)."""
    start = len("#SBATCH")
    m = re.search(r"\s#", line[start:])
    end = start + m.start() if m else len(line)
    return start, end


def parse_directive_line(line: str, line_index: int) -> List[Directive]:
    """Parsea las opciones de una línea `#SBATCH ...`."""
    start, end = _options_region(line)
    tokens = [(m.group(0), m.start() + start, m.end() + start) for m in re.finditer(r"\S+", line[start:end])]
    result: List[Directive] = []
    i = 0
    while i < len(tokens):
        tok, t_start, t_end = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None

        if tok.startswith("--") and len(tok) > 2:
            body = tok[2:]
            if "=" in body:
                name, value = body.split("=", 1)
                canon, known = _resolve_long(name)
                result.append(Directive(line_index, canon, value, (t_start, t_end), "long=", tok, known))
            else:
                canon, known = _resolve_long(body)
                takes_value = canon in LONG_WITH_VALUE and canon not in OPTIONAL_VALUE
                if not known:
                    # Opción desconocida: si le sigue algo que no es opción, se asume que es su valor
                    takes_value = nxt is not None and not nxt[0].startswith("-")
                if takes_value and nxt is not None:
                    result.append(Directive(line_index, canon, nxt[0], (t_start, nxt[2]), "long", f"{tok} {nxt[0]}", known))
                    i += 1
                else:
                    result.append(Directive(line_index, canon, None, (t_start, t_end), "long", tok, known))
        elif tok.startswith("-") and len(tok) > 1:
            letter = tok[1]
            if letter in SHORT_WITH_VALUE:
                canon = SHORT_WITH_VALUE[letter]
                if len(tok) > 2:
                    value = tok[2:].lstrip("=")
                    result.append(Directive(line_index, canon, value, (t_start, t_end), "short", tok))
                elif nxt is not None:
                    result.append(Directive(line_index, canon, nxt[0], (t_start, nxt[2]), "short", f"{tok} {nxt[0]}"))
                    i += 1
                else:
                    result.append(Directive(line_index, canon, None, (t_start, t_end), "short", tok))
            elif letter in SHORT_FLAGS:
                result.append(Directive(line_index, SHORT_FLAGS[letter], None, (t_start, t_end), "short", tok))
            else:
                result.append(Directive(line_index, tok, None, (t_start, t_end), "short", tok, known=False))
        i += 1
    return result


def _is_command(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def parse_sbatch(content: str) -> SbatchScript:
    """Parsea un script completo respetando las reglas de lectura de sbatch."""
    lines = content.splitlines()
    header_end = len(lines)
    for idx, line in enumerate(lines):
        if _is_command(line):
            header_end = idx
            break

    script = SbatchScript(lines=lines, header_end=header_end)
    for idx, line in enumerate(lines):
        if line.startswith("#SBATCH"):
            parsed = parse_directive_line(line, idx)
            if idx < header_end:
                script.directives.extend(parsed)
            else:
                script.ignored.extend(parsed)
        elif idx >= header_end and _is_command(line):
            # Se descarta el comentario final de la línea de comando
            script.command_lines.append((idx, re.split(r"\s#", line, maxsplit=1)[0]))
    return script


def format_directive(name: str, value: Optional[str], style: str = "long=") -> str:
    """Formatea una opción para escribirla en una línea #SBATCH respetando el estilo original."""
    short = {v: k for k, v in SHORT_WITH_VALUE.items()}
    if value is None:
        return f"--{name}"
    if style == "short" and name in short:
        return f"-{short[name]} {value}"
    if style == "long":
        return f"--{name} {value}"
    return f"--{name}={value}"


def job_log_files(content: str, job_id: int, job_name: str) -> List[str]:
    """
    Archivos de salida y error de un trabajo según sus #SBATCH -o / -e, con los patrones
    `%j`/`%J` (Job ID) y `%x` (nombre del job) resueltos. Sin -o, Slurm usa `slurm-%j.out`;
    sin -e, los errores van al mismo archivo de salida. Las rutas relativas son respecto de
    la carpeta desde la que se envió el trabajo.
    """
    script = parse_sbatch(content)

    def resolve(pattern: str) -> str:
        return pattern.replace("%j", str(job_id)).replace("%J", str(job_id)).replace("%x", job_name)

    output = resolve(script.value("output") or "slurm-%j.out")
    error = resolve(script.value("error") or output)
    return [output] if error == output else [output, error]
