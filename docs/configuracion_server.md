# Despliegue de Cluster-AI en el nodo cabecera (CentOS Linux 8)

> **Estado:** el helper privilegiado (`modules/privileged/`) y el cliente que lo invoca están implementados (ver [sección 8](#8-estado-de-la-implementación)). La regla de `sudo` debe apuntar **únicamente** al lanzador del helper (paso E), nunca al CLI `cluster-ai`: correr el CLI completo como usuario de servicio permitiría leer la API Key y modificar la base (ver [sección 2](#2-por-qué-no-alcanza-con-correr-todo-el-cli-como-usuario-de-servicio)).
>
> **Alcance de esta fase:** el investigador recibe optimizaciones **solo de su script `#SBATCH`** (directivas y recursos). La auditoría del código fuente del proyecto no está habilitada.
>
> **Guía rápida:** los comandos de instalación y actualización, en orden y sin explicaciones extensas, están en [guia_rapida_instalacion.md](guia_rapida_instalacion.md).

---

## 1. Objetivo y modelo de amenaza

En el servidor compartido, cualquier investigador tiene shell propia. Se busca que:

| Requisito | Significado concreto |
| :--- | :--- |
| **API Key oculta** | Ningún investigador puede leer la clave del proveedor de IA, ni directa ni indirectamente (a través de la herramienta). |
| **Integridad de la base** | Nadie puede editar, borrar ni reescribir `cluster.db` a mano. Los registros solo entran por operaciones validadas. |
| **Trazabilidad real** | Cada registro queda asociado al usuario Linux que lo originó, sin posibilidad de suplantar a otro. |
| **Uso transparente** | El investigador escribe `cluster-ai diagnose job.sh` como cualquier comando, sin claves ni pasos extra. |

**Atacante considerado:** un usuario del clúster con shell, que puede escribir scripts arbitrarios, invocar cualquier binario con cualquier argumento y manipular sus variables de entorno. No se considera un atacante con `root`.

---

## 2. Por qué no alcanza con correr todo el CLI como usuario de servicio

El esquema “wrapper que hace `sudo -u cluster-ai cluster-ai "$@"`” protege los archivos del acceso **directo**, pero el proceso privilegiado queda haciendo trabajo **en nombre del investigador**, con datos controlados por él:

1. **`short-run` ejecuta el script del usuario** (`bash script.sh`) → correría como `cluster-ai`, y un `cat` del archivo de secretos dentro del script alcanza para robar la clave.
2. **`run` hace `sbatch`** → el job se encolaría como `cluster-ai`: ejecución arbitraria en los nodos con la identidad del servicio y horas-núcleo imputadas a la cuenta equivocada.
3. **`diagnose` lee cualquier ruta** que reciba (incluido el archivo de secretos) y envía su contenido al LLM, que lo devuelve por consola. A la vez, no podría leer scripts ni escribir `.optimized.sh` e informes en homes con permisos `700`.
4. **Rutas relativas:** `sudo` conserva el directorio de trabajo, así que `CLUSTER_AI_DB_PATH=cluster.db` terminaría resolviéndose en la carpeta del investigador.

La conclusión es que **el privilegio tiene que reducirse a lo mínimo indispensable**: usar la API Key y escribir en la base. Todo lo demás debe correr con la identidad del propio investigador.

---

## 3. Arquitectura: separación de privilegios

```
 investigador (uid real)                          cluster-ai (usuario de servicio)
┌─────────────────────────────────────┐          ┌──────────────────────────────────────┐
│ cluster-ai  (CLI cliente)           │  sudo    │ cluster-ai-priv  (helper mínimo)     │
│  • lee job.sh                       │ ───────► │  • obtiene la identidad de SUDO_UID  │
│  • linter, diff, .optimized         │  stdin:  │  • lee /etc/cluster-ai/*.env         │
│  • escribe el informe Markdown      │  JSON    │  • arma el prompt y llama a la IA    │
│  • sbatch / short-run propios       │ ◄─────── │  • sacct verificado + cluster.db     │
│  • NO tiene la API Key ni la base   │  stdout: │  • NO acepta rutas, NO ejecuta nada  │
└─────────────────────────────────────┘  JSON    └──────────────────────────────────────┘
```

| Operación | Quién la ejecuta | Identidad |
| :--- | :--- | :--- |
| Leer el script `#SBATCH` | Cliente | Investigador |
| Crear `.optimized.sh` y `*_INFORME_MEJORA.md` (y conservar sus versiones previas) | Cliente | Investigador |
| `sbatch`, `short-run`, envío de `benchmark` | Cliente | Investigador |
| Revisión con IA del `#SBATCH` (usa la API Key) | Helper | `cluster-ai` |
| `sacct`/`seff` para importar telemetría (verificando dueño del job) | Helper | `cluster-ai` |
| Guardar análisis y telemetría; leer historial y comparativas | Helper | `cluster-ai` |

---

## 4. Contrato del helper `cluster-ai-priv`

El helper es la **única** superficie privilegiada. Debe asumirse que el investigador lo invocará directamente con entradas maliciosas, sin pasar por el cliente.

### Reglas de diseño

- **Identidad:** se obtiene exclusivamente de `SUDO_UID` (→ `pwd.getpwuid()`), que `sudo` fija a partir del UID real del proceso invocante. Nunca de `USER`, `LOGNAME` ni `USERNAME`; en CentOS, `USERNAME` está en la lista `env_keep` por defecto de `sudo` y **el usuario puede falsearla**. Si `SUDO_UID` no está presente, el helper aborta.
- **Entrada:** solo JSON por `stdin`, con tamaño máximo (por ejemplo, 256 KiB) y validado con modelos Pydantic. Los argumentos de línea de comandos se limitan al nombre del subcomando y enteros.
- **Sin rutas ni ejecución:** no recibe rutas de archivo, no abre archivos del usuario, no ejecuta scripts ni `sbatch`.
- **Configuración fija:** lee la configuración desde una ruta absoluta (`/etc/cluster-ai/cluster-ai.env`), nunca la busca a partir del directorio actual. La base se abre con ruta absoluta.
- **Entorno neutro:** hace `cd /` al iniciar y corre con `python -I` (ignora `PYTHONPATH`, `PYTHON*` y el directorio actual).
- **Campo `user`:** cualquier valor de usuario que venga en el JSON se ignora y se reemplaza por la identidad derivada de `SUDO_UID`.

### Subcomandos

Todas las respuestas tienen la forma `{"ok": true, "data": ...}` o `{"ok": false, "error": "..."}`.

| Subcomando | Entrada (stdin) | `data` de la respuesta | Notas |
| :--- | :--- | :--- | :--- |
| `whoami` | — | `{"user": "npereyra", "is_admin": false}` | Diagnóstico de la instalación. |
| `review-sbatch` | `{"script_name": "job.sh", "sbatch_content": "...", "issues_summary": "..."}` | `{"content": "...", "model": "..."}` | Único uso de la API Key. El **helper arma el prompt** (solo reglas de Slurm y del clúster), así que no sirve como proxy genérico del LLM. `script_name` debe ser un nombre sin `/`; contenido ≤ 64 KiB; límite de consultas por usuario y hora. |
| `save-analysis` | `{"script_path", "script_hash", "backup_path", "optimized_script_path", "report_markdown_path", "recommended_partition", "report_content"}` | `{"report_id": N}` | Se registra con el usuario de `SUDO_UID`; un campo `user` en la entrada se rechaza. |
| `sync-job <job_id>` | — | Telemetría guardada | Acepta solo un entero. El helper ejecuta `sacct`/`seff` y verifica que el job sea del invocante y esté finalizado. |
| `compare <base> <opt>` | — | Comparativa | Importa los jobs que falten (con la misma verificación) y rechaza jobs ajenos. |
| `history` | `{"limit": 20, "user": null}` | `{"runs": [...]}` | Cada usuario ve solo lo suyo; los miembros de `CLUSTER_AI_ADMIN_GROUP` ven todo. |

**Integridad de la telemetría:** el clúster **no** define `PrivateData=jobs`, por lo que `cluster-ai` puede consultar cualquier job con `sacct` sin privilegios de Slurm adicionales. El helper usa esa visibilidad para leer la telemetría directamente de Slurm (nunca del cliente) y solo la guarda si el campo `User` del job coincide con el invocante. Si en el futuro se activara `PrivateData=jobs`, `sync-job` dejaría de encontrar los jobs de los investigadores y habría que revisar este diseño.

---

## 5. Particularidades de CentOS Linux 8

- **Fin de soporte:** CentOS Linux 8 llegó a su EOL el **31/12/2021**. Los repositorios oficiales ya no están en `mirror.centos.org` y `dnf` falla salvo que se apunte a `vault.centos.org`. Tampoco recibe parches de seguridad (incluido `sudo`, que es justamente la pieza de confianza de este esquema). Se recomienda planificar la migración a Rocky Linux 8 o AlmaLinux 8 (migración in-place con `migrate2rocky` / `almalinux-deploy`); todo este documento aplica igual en esas distribuciones.
- **Python 3.11 no está en los repositorios** de CentOS 8 (el AppStream llega a `python39`). El proyecto requiere `>=3.11`, por lo que se usa **`uv`** con un intérprete gestionado (python-build-standalone, compatible con la glibc 2.28 de EL8), instalado en una ruta compartida (no en `/root`).
- **`sudo` 1.8.29** con los defaults de RHEL: `env_reset`, `always_set_home`, `secure_path=/sbin:/bin:/usr/sbin:/usr/bin` y un `env_keep` amplio (`LANG`, `LC_*`, `USERNAME`, `DISPLAY`, …). El archivo `/etc/sudoers` ya incluye `#includedir /etc/sudoers.d`. `requiretty` no está activo por defecto en EL8; si alguien lo agregó, el helper fallaría al invocarse desde scripts sin TTY: revisar con `grep -r requiretty /etc/sudoers /etc/sudoers.d`.
- **SELinux** suele estar en modo `Enforcing`. Los usuarios del clúster normalmente corren como `unconfined_u`, y `sudo` conserva ese contexto, por lo que no hace falta política propia. Sí hay que restaurar los contextos de los archivos copiados (paso H). Si aparece *Permission denied* con permisos Unix correctos, revisar `ausearch -m avc -ts recent`.
- **Auditoría:** cada invocación del helper queda registrada por `sudo` en `/var/log/secure`.
- **Proxy de salida:** si el nodo cabecera sale a Internet por un proxy, `env_reset` descarta `HTTPS_PROXY` del entorno del investigador. Hay que definirlo en el archivo de secretos del helper (paso D); `httpx` lo toma de las variables de entorno.

---

## 6. Instalación paso a paso (como `root`)

### Paso A — Usuario de servicio

```bash
useradd --system --shell /sbin/nologin --home-dir /var/lib/cluster-ai --no-create-home cluster-ai
```

Opcional: crear un grupo para restringir quién puede usar la herramienta (ver paso F).

```bash
groupadd cluster-ai-users
```

### Paso B — `uv`, Python 3.11 y código (propiedad de `root`, solo lectura para el resto)

El código y el entorno virtual **no** pertenecen a `cluster-ai`: así, aunque el helper se viera comprometido, no podría modificar su propio código.

```bash
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
```

```bash
git clone <url-del-repositorio> /opt/cluster-ai
```

```bash
cd /opt/cluster-ai && UV_PYTHON_INSTALL_DIR=/opt/cluster-ai/python UV_LINK_MODE=copy uv sync --frozen --no-dev
```

```bash
chown -R root:root /opt/cluster-ai && chmod -R u=rwX,go=rX /opt/cluster-ai
```

- `UV_PYTHON_INSTALL_DIR` deja el intérprete dentro de `/opt/cluster-ai/python`, accesible para todos. Si quedara en `/root/.local/share/uv`, el symlink `.venv/bin/python` no funcionaría para nadie más que root.
- `uv sync` instala el proyecto en modo editable, por lo que `knowledge/` y `config/` se resuelven desde `/opt/cluster-ai`.
- **No debe existir ningún `.env` dentro de `/opt/cluster-ai`**: ese directorio es legible por todos. Verificar con `ls -la /opt/cluster-ai/.env` (debe dar *No such file*).

### Paso C — Directorio de datos (solo `cluster-ai`)

```bash
install -d -o cluster-ai -g cluster-ai -m 0700 /var/lib/cluster-ai
```

La base `cluster.db` y sus archivos auxiliares de SQLite (`-journal`, `-wal`, `-shm`) se crean ahí en el primer uso, protegidos por los permisos del directorio. No hace falta crear el archivo de antemano.

### Paso D — Secretos y configuración del helper

```bash
install -d -o root -g cluster-ai -m 0750 /etc/cluster-ai
```

```bash
install -o root -g cluster-ai -m 0640 /dev/null /etc/cluster-ai/cluster-ai.env
```

Editar `/etc/cluster-ai/cluster-ai.env`:

```ini
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
# Ruta SIEMPRE absoluta (el helper rechaza rutas relativas)
CLUSTER_AI_DB_PATH=/var/lib/cluster-ai/cluster.db
# Directorio de sacct/seff (ruta absoluta; en EL8 con RPMs de Slurm suele ser /usr/bin)
CLUSTER_AI_SLURM_BIN_DIR=/usr/bin
# Grupo cuyos miembros pueden ver el historial de todos los usuarios (opcional)
CLUSTER_AI_ADMIN_GROUP=wheel
# Consultas a la IA por usuario y por hora
CLUSTER_AI_MAX_AI_REQUESTS_PER_HOUR=30
# Solo si el nodo sale a Internet por proxy
# HTTPS_PROXY=http://proxy.ialp.local:3128
```

`cluster-ai` puede leer el archivo pero no modificarlo; los investigadores no pueden ni listarlo. La verificación TLS hacia el proveedor de IA está activa por defecto; no desactivarla (`OPENAI_TLS_VERIFY=false`) salvo para un endpoint local de pruebas. Confirmar la ubicación de los binarios con `command -v sacct seff`.

### Paso E — Lanzador del helper (propiedad de `root`)

```bash
cat > /usr/local/libexec/cluster-ai-priv << 'EOF'
#!/bin/bash
# Lanzador del helper privilegiado de Cluster-AI. Se ejecuta vía sudo como cluster-ai.
cd / || exit 1
exec /opt/cluster-ai/.venv/bin/python -I -m modules.privileged "$@"
EOF
```

```bash
chown root:root /usr/local/libexec/cluster-ai-priv && chmod 0755 /usr/local/libexec/cluster-ai-priv
```

`-I` (modo aislado) hace que Python ignore `PYTHONPATH`/`PYTHON*` y no agregue el directorio actual a `sys.path`, de modo que un investigador no puede “inyectar” módulos con un `modules/` falso en su carpeta.

### Paso F — Regla de `sudo`

Crear la regla **siempre con `visudo`**, que valida la sintaxis antes de guardar (un error en `sudoers` puede dejar el servidor sin `sudo`):

```bash
EDITOR=nano visudo -f /etc/sudoers.d/cluster-ai
```

(`EDITOR=nano` usa nano en lugar de vi; nunca editar archivos de sudoers directamente con un editor, sin `visudo`.)

Contenido (elegir una de las dos líneas):

```
# Todos los usuarios del servidor:
ALL                ALL=(cluster-ai) NOPASSWD: /usr/local/libexec/cluster-ai-priv
# o solo los miembros del grupo cluster-ai-users:
# %cluster-ai-users ALL=(cluster-ai) NOPASSWD: /usr/local/libexec/cluster-ai-priv
```

```bash
chmod 0440 /etc/sudoers.d/cluster-ai && visudo -c
```

La regla autoriza **solo** el lanzador como `cluster-ai`; no habilita shells, `sbatch` ni el CLI completo.

### Paso G — Comando para los investigadores (sin `sudo`)

El CLI cliente corre con la identidad del investigador; él mismo invoca al helper cuando necesita la IA o la base. Detecta el helper automáticamente si existe `/usr/local/libexec/cluster-ai-priv` (se puede forzar otra ruta con `CLUSTER_AI_PRIV_HELPER=<ruta>` o desactivarlo con `CLUSTER_AI_PRIV_HELPER=off`, lo que solo afecta al propio usuario).

```bash
ln -sf /opt/cluster-ai/.venv/bin/cluster-ai /usr/local/bin/cluster-ai && ln -sf /opt/cluster-ai/.venv/bin/cai /usr/local/bin/cai
```

`/usr/local/bin` está en el `PATH` por defecto de los usuarios en CentOS 8.

### Paso H — Contextos SELinux

```bash
restorecon -Rv /opt/cluster-ai /var/lib/cluster-ai /etc/cluster-ai /usr/local/libexec/cluster-ai-priv /etc/sudoers.d/cluster-ai
```

---

## 7. Verificación (como un investigador cualquiera)

| Prueba | Resultado esperado |
| :--- | :--- |
| `cat /etc/cluster-ai/cluster-ai.env` | `Permission denied` |
| `ls /var/lib/cluster-ai` | `Permission denied` |
| `sqlite3 /var/lib/cluster-ai/cluster.db .tables` | Error de permisos / no se puede abrir |
| `sudo -l` | Muestra únicamente `(cluster-ai) NOPASSWD: /usr/local/libexec/cluster-ai-priv` |
| `sudo -u cluster-ai /bin/bash` | Rechazado (no permitido) |
| `sudo -u cluster-ai /usr/local/libexec/cluster-ai-priv whoami` | `{"ok": true, "data": {"user": "<tu usuario>", ...}}` |
| `USERNAME=otro sudo -u cluster-ai /usr/local/libexec/cluster-ai-priv whoami` | Sigue devolviendo **tu** usuario |
| `/usr/local/libexec/cluster-ai-priv whoami` (sin sudo) | `{"ok": false, ...}`: no puede leer la configuración |
| `sudo -u cluster-ai /usr/local/libexec/cluster-ai-priv sync-job <job de otro>` | `"El Job N pertenece a otro usuario."` |
| `cluster-ai diagnose job.sh` en tu home | Genera `.optimized.sh` e informe **propiedad tuya** (sin `.bak`); el informe aclara que solo se optimizó el `#SBATCH` |
| `cluster-ai run job.sh` y luego `squeue -u $USER` | El job figura a **tu** nombre, no a `cluster-ai` |
| `cluster-ai history` | Solo tus corridas |
| `grep cluster-ai-priv /var/log/secure` (como root) | Una línea por invocación, con usuario y comando |

---

## 8. Estado de la implementación

| Pieza | Ubicación | Qué hace |
| :--- | :--- | :--- |
| Helper privilegiado | `modules/privileged/helper.py`, `__main__.py` | Contrato de la sección 4. Identidad por `SUDO_UID` y verificación de que corre como `cluster-ai`; configuración desde ruta fija; `cd /` y `umask 077`; JSON validado con Pydantic (`shared/models.py`); límite de IA por usuario (tabla `ai_usage`). |
| Cliente del helper | `modules/engine/privileged_client.py` | Invoca `sudo -n -u cluster-ai …` y traduce errores (incluida la falta de regla de sudo). |
| Orquestación | `modules/engine/runner.py` | Con helper: `diagnose` usa `review-sbatch`/`save-analysis`; `sync`, `compare` e `history` se delegan. Sin helper o en `--mock`: modo local de desarrollo. `run`, `short-run` y el envío y la espera de `benchmark` siempre corren como el investigador (la comparativa final se delega como `compare`). |
| Alcance `#SBATCH` | `shared/features.py`, `modules/engine/context.py`, `modules/reporters/markdown.py` | Prompt solo de directivas, sin leer código del proyecto. La auditoría de código solo puede activarse en desarrollo local con `CLUSTER_AI_ENABLE_CODE_AUDIT=true`; con el helper es imposible. |
| Telemetría verificada | `modules/inspectors/slurm_inspector.py` | Binarios por ruta absoluta, sin caída al emulador en el helper, y solo jobs finalizados. |
| Rutas de base | `modules/engine/storage.py` | Las rutas relativas se anclan a la raíz del proyecto, nunca al directorio actual. La base mock va a `~/.local/share/cluster-ai/` si el proyecto no es escribible (caso del servidor). |
| Identidad local | `shared/identity.py` | UID real del proceso en vez de `USER`/`USERNAME`. |
| Cliente IA | `modules/engine/ai.py` | Verificación TLS activa y la API Key se elimina de cualquier mensaje de error. |

Pruebas: `tests/test_privileged.py` y `tests/test_sbatch_scope.py`.

**Pendiente de validar en el servidor real:** la instalación de los pasos A–H y la tabla de la sección 7. Las pruebas automáticas cubren el helper con servicios simulados, no `sudo` ni Slurm reales.

---

## 9. Riesgos residuales

- **Uso indebido de la IA:** el helper arma el prompt y solo acepta contenido de un script `#SBATCH`, pero un usuario puede poner texto arbitrario en ese campo para intentar otro uso del modelo. Se mitiga con el tamaño máximo, el límite de consultas por hora, la instrucción de sistema que trata el script como dato y el registro por usuario en `ai_usage`.
- **Datos auto-reportados:** los análisis de `diagnose` los produce el cliente, que el usuario controla. Pueden ser falsos, pero siempre quedan atribuidos a su autor real. La telemetría de jobs, en cambio, la obtiene el helper directamente de Slurm.
- **Nodos de cómputo:** el helper y la regla de `sudo` se instalan solo en el nodo cabecera. Si se invoca `cluster-ai` dentro de un job, las funciones de IA y de base no estarán disponibles (comportamiento deseado).
- **`root`** puede leer todo; está fuera del modelo de amenaza.
- **Sistema operativo sin soporte:** mientras el servidor siga en CentOS 8, cualquier vulnerabilidad nueva de `sudo` o del kernel queda sin parche (ver sección 5).

---

## 10. Alternativa: servicio `systemd` con socket UNIX

Si en el futuro se necesitan colas internas, cachés en memoria o reintentos ante caídas del proveedor, el helper puede transformarse en un servicio `systemd` que corre como `cluster-ai` y escucha en `/run/cluster-ai/cluster-ai.sock`. La identidad del cliente se obtiene con `SO_PEERCRED` (UID del proceso conectado, provisto por el kernel), y desaparece la dependencia de `sudo`.

El contrato de la sección 4 es el mismo: solo cambia el transporte (socket en lugar de `sudo` + stdin/stdout). En CentOS 8 con SELinux en `Enforcing`, un servicio `systemd` sí corre en un dominio confinado, por lo que puede requerir ajustar contextos (`semanage fcontext`) o un módulo de política local. Para la etapa actual, el helper vía `sudo` es más simple y no deja procesos corriendo en segundo plano.
