# Guía rápida de instalación de Cluster-AI (head001, CentOS 8)

Esta guía junta, en orden, los comandos para instalar Cluster-AI en el nodo cabecera. El detalle de cada decisión (modelo de seguridad, contrato del helper, riesgos) está en el manual completo: [configuracion_server.md](configuracion_server.md).

Salvo que se indique otra cosa, **todos los comandos se ejecutan como root** (`sudo -i`; el prompt termina en `#`).

---

## Qué queda instalado y dónde

| Ubicación | Dueño / permisos | Qué es |
| :--- | :--- | :--- |
| Usuario y grupo `cluster-ai` | sistema, shell `/sbin/nologin` | Usuario de servicio. No inicia sesión; solo lo usa el helper vía sudo. |
| `/opt/cluster-ai/` | `root:root` `755` | Repositorio clonado (código, `config/`, `knowledge/`). |
| `/opt/cluster-ai/.venv/` | `root:root` `755` | Entorno virtual con las dependencias (lo crea `uv sync`). |
| `/opt/cluster-ai/python/` | `root:root` `755` | Python 3.11 gestionado por `uv` (CentOS 8 no lo trae). |
| `/var/lib/cluster-ai/` | `cluster-ai:cluster-ai` `700` | Directorio de datos y home del usuario de servicio. |
| `/var/lib/cluster-ai/cluster.db` (+ `-wal`, `-shm`) | `cluster-ai` `600` | Base de datos de producción (se crea en el primer uso). |
| `/etc/cluster-ai/` | `root:cluster-ai` `750` | Configuración del servicio. |
| `/etc/cluster-ai/cluster-ai.env` | `root:cluster-ai` `640` | API Key y configuración del helper. **Secreto.** |
| `/usr/local/libexec/cluster-ai-priv` | `root:root` `755` | Lanzador del helper privilegiado. |
| `/etc/sudoers.d/cluster-ai` | `root:root` `440` | Regla que permite ejecutar **solo** el lanzador como `cluster-ai`. |
| `/usr/local/bin/cluster-ai`, `/usr/local/bin/cai` | enlaces simbólicos | Comandos que usan los investigadores (apuntan al venv). |
| `/usr/local/bin/uv`, `/usr/local/bin/uvx` | `root` | Herramienta de instalación de Python y dependencias. |
| `/root/.cache/uv/` | `root` | Caché de descargas de `uv` (se puede borrar). |
| `/root/.ssh/cluster-ai-deploy` (+ `.pub`) | `root` `600` | Solo si el repo es privado: deploy key de solo lectura. |
| `~/.local/share/cluster-ai/cluster_mock.db` | cada investigador | Solo si alguien usa `--mock`: base de pruebas personal. |
| `/var/log/secure` | sistema | Registro de cada invocación del helper vía sudo (auditoría). |

---

## 0. Relevamiento previo (no modifica nada)

Confirma que no hay nada que trabe la instalación. Resultados en head001: SELinux `Disabled`, sudo `1.8.29`, Slurm en `/usr/bin`, `PrivateData = none`, sin `requiretty`, salida directa a Internet.

```bash
getenforce
```
```bash
sudo -V | head -1
```
```bash
command -v sacct seff sinfo
```
```bash
scontrol show config | grep -i PrivateData
```
```bash
grep -r requiretty /etc/sudoers /etc/sudoers.d
```
```bash
git --version
```
```bash
curl -sI https://github.com | head -1
```
```bash
curl -sI https://api.openai.com/v1/models | head -1
```

Un `401` en el último comando es correcto: significa que el proveedor de IA responde (falta la clave, que va en el paso D).

---

## A. Usuario de servicio

Crea el usuario `cluster-ai` sin login. Su home es el directorio de datos (se crea en el paso C); el repositorio **no** va ahí, sino en `/opt`, propiedad de root.

```bash
useradd --system --shell /sbin/nologin --home-dir /var/lib/cluster-ai --no-create-home cluster-ai
```

---

## B. uv, Python 3.11 y código

Instala `uv` en `/usr/local/bin`.

```bash
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
```

Clona el repositorio en `/opt/cluster-ai`. Si el repo es **público**:

```bash
git clone https://github.com/nehuenpereyra/cluster-ai.git /opt/cluster-ai
```

Si el repo es **privado**, generar una deploy key, cargar el `.pub` en GitHub (Settings → Deploy keys, **sin** permiso de escritura) y clonar con ella. No copiar claves personales a `/root` ni poner tokens en la URL.

```bash
ssh-keygen -t ed25519 -N "" -C "head001-deploy" -f /root/.ssh/cluster-ai-deploy
```
```bash
cat /root/.ssh/cluster-ai-deploy.pub
```
```bash
GIT_SSH_COMMAND="ssh -i /root/.ssh/cluster-ai-deploy -o IdentitiesOnly=yes" git clone git@github.com:nehuenpereyra/cluster-ai.git /opt/cluster-ai
```

Instala Python 3.11 y las dependencias **dentro de** `/opt/cluster-ai`, para que sean accesibles para todos los usuarios.

```bash
cd /opt/cluster-ai && UV_PYTHON_INSTALL_DIR=/opt/cluster-ai/python UV_LINK_MODE=copy uv sync --frozen --no-dev
```

Deja todo el código propiedad de root y de solo lectura para el resto.

```bash
chown -R root:root /opt/cluster-ai && chmod -R u=rwX,go=rX /opt/cluster-ai
```

Verifica que el Python quedó en `/opt` (la ruta debe empezar con `/opt/cluster-ai/python/`).

```bash
readlink -f /opt/cluster-ai/.venv/bin/python
```

Verifica que no haya un `.env` en `/opt` (debe dar *No such file or directory*) y que el helper se importe bien (debe imprimir `ok`).

```bash
ls -la /opt/cluster-ai/.env
```
```bash
/opt/cluster-ai/.venv/bin/python -I -c "import modules.privileged.helper; print('ok')"
```

---

## C. Directorio de datos

Crea el directorio privado donde vivirá `cluster.db`.

```bash
install -d -o cluster-ai -g cluster-ai -m 0700 /var/lib/cluster-ai
```

---

## D. Configuración y API Key

Crea el directorio y el archivo de configuración con los permisos correctos (vacío).

```bash
install -d -o root -g cluster-ai -m 0750 /etc/cluster-ai
```
```bash
install -o root -g cluster-ai -m 0640 /dev/null /etc/cluster-ai/cluster-ai.env
```

Carga el contenido **con un editor**, nunca con `echo` ni heredoc (quedaría la API Key en el historial de bash).

```bash
nano /etc/cluster-ai/cluster-ai.env
```

Contenido (ajustar proveedor, modelo y grupo de administradores):

```ini
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
CLUSTER_AI_DB_PATH=/var/lib/cluster-ai/cluster.db
CLUSTER_AI_SLURM_BIN_DIR=/usr/bin
CLUSTER_AI_ADMIN_GROUP=wheel
CLUSTER_AI_MAX_AI_REQUESTS_PER_HOUR=30
```

`CLUSTER_AI_ADMIN_GROUP` define quién ve el historial de todos los usuarios (confirmar la pertenencia con `id <usuario>`). No hace falta `HTTPS_PROXY` porque head001 tiene salida directa.

---

## E. Lanzador del helper

Crea el lanzador que invoca el helper con Python en modo aislado.

```bash
install -d -m 0755 /usr/local/libexec
```
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

### Prueba como root (antes de crear la regla de sudo)

Identidad: debe devolver `{"ok": true, "data": {"user": "root", "is_admin": true}}`.

```bash
sudo -u cluster-ai /usr/local/libexec/cluster-ai-priv whoami
```

Conexión con la IA y creación de la base: debe traer `"ok": true` y un texto con recomendaciones. Si empieza con *"Modo Contingencia Heurístico Activado"*, falló la clave o la conexión (el motivo aparece en esa línea).

```bash
echo '{"script_name":"test.sh","sbatch_content":"#!/bin/bash\n#SBATCH -p short\n#SBATCH -t 00:10:00\nsrun hostname"}' | sudo -u cluster-ai /usr/local/libexec/cluster-ai-priv review-sbatch
```

La base debe aparecer con permisos `-rw-------` y dueño `cluster-ai`.

```bash
ls -l /var/lib/cluster-ai
```

---

## F. Regla de sudo

Antes de empezar, **dejar abierta una segunda sesión como root** por si algo sale mal.

> **Importante:** los archivos de sudoers se editan **siempre** con `visudo`, que valida la sintaxis antes de guardar. Un error de sintaxis puede dejar `sudo` inutilizable. Para usar nano en vez de vi:
>
> ```bash
> EDITOR=nano visudo -f /etc/sudoers.d/cluster-ai
> ```

Escribir **una** de estas dos líneas: la primera habilita a todos los usuarios; la segunda, solo a los del grupo `cluster-ai-users` (crearlo antes con `groupadd cluster-ai-users` y sumar usuarios con `usermod -aG cluster-ai-users <usuario>`).

```
ALL ALL=(cluster-ai) NOPASSWD: /usr/local/libexec/cluster-ai-priv
```
```
%cluster-ai-users ALL=(cluster-ai) NOPASSWD: /usr/local/libexec/cluster-ai-priv
```

Ajusta permisos y valida todo sudoers (debe terminar en `parsed OK` en cada archivo).

```bash
chmod 0440 /etc/sudoers.d/cluster-ai && visudo -c
```

---

## G. Comando para los investigadores

Crea los comandos `cluster-ai` y `cai` en el `PATH` de todos (sin sudo: corren con la identidad de cada investigador).

```bash
ln -sf /opt/cluster-ai/.venv/bin/cluster-ai /usr/local/bin/cluster-ai && ln -sf /opt/cluster-ai/.venv/bin/cai /usr/local/bin/cai
```

**Modo producción:** como existe el helper (`/usr/local/libexec/cluster-ai-priv`), el CLI detecta solo que está en producción y oculta el emulador: no aparece el comando `mock` ni las opciones `--mock`. No hace falta configurar nada. Para fijarlo explícitamente para todos los usuarios (opcional):

```bash
echo 'export CLUSTER_AI_ENV=production' > /etc/profile.d/cluster-ai.sh && chmod 0644 /etc/profile.d/cluster-ai.sh
```

Verificar como investigador que `cluster-ai --help` no muestre `mock` ni `--mock`.

```bash
cluster-ai --help
```

## H. SELinux (solo si está activo)

En head001 `getenforce` da `Disabled`, así que **este paso se omite**. Si algún día se activa:

```bash
restorecon -Rv /opt/cluster-ai /var/lib/cluster-ai /etc/cluster-ai /usr/local/libexec/cluster-ai-priv /etc/sudoers.d/cluster-ai
```

---

## Verificación como investigador

Cambiar a un usuario común (no pide su contraseña desde root; `exit` para volver).

```bash
su - user001
```

No debe poder leer la configuración ni la base (*Permission denied* en ambos).

```bash
cat /etc/cluster-ai/cluster-ai.env
```
```bash
ls /var/lib/cluster-ai
```

`sudo -l` debe mostrar solo `(cluster-ai) NOPASSWD: /usr/local/libexec/cluster-ai-priv`.

```bash
sudo -l
```

La identidad no se puede falsear: debe devolver el usuario real, no `otro`.

```bash
USERNAME=otro sudo -u cluster-ai /usr/local/libexec/cluster-ai-priv whoami
```

No puede abrir una shell como `cluster-ai`: pide contraseña y, aunque se la diera, sería rechazado.

```bash
sudo -u cluster-ai /bin/bash
```

Diagnóstico de un script real: genera `.bak_*`, `.optimized.*` e informe junto al script, **propiedad del investigador**.

```bash
cluster-ai diagnose job.mpi
```
```bash
ls -l job.mpi.bak_* job.optimized.mpi job_INFORME_MEJORA.md
```

Slurm debe aceptar el script optimizado con todos sus recursos (no encola nada; el JobID que muestra es solo una simulación).

```bash
sbatch --test-only job.optimized.mpi
```

El historial muestra solo las corridas del propio usuario.

```bash
cluster-ai history
```

---

## Actualizar a una nueva versión del repositorio

**1. En la máquina de desarrollo**, commitear y subir los cambios. Si se agregaron o cambiaron dependencias en `pyproject.toml`, regenerar primero `uv.lock` (el servidor instala con `--frozen` y falla si el lock está desactualizado).

```bash
uv lock
```
```bash
git add -A && git commit -m "<mensaje>" && git push
```

**2. En el servidor, como root**, traer los cambios, sincronizar dependencias y restaurar dueños y permisos. Si el repo es privado y se clonó con deploy key, anteponer `GIT_SSH_COMMAND="ssh -i /root/.ssh/cluster-ai-deploy -o IdentitiesOnly=yes"` al `git pull`.

```bash
cd /opt/cluster-ai && git pull && UV_PYTHON_INSTALL_DIR=/opt/cluster-ai/python UV_LINK_MODE=copy uv sync --frozen --no-dev && chown -R root:root /opt/cluster-ai && chmod -R u=rwX,go=rX /opt/cluster-ai
```

**3. Verificar** que el helper sigue funcionando.

```bash
sudo -u cluster-ai /usr/local/libexec/cluster-ai-priv whoami
```

Notas:

- El `git pull` no toca la API Key (`/etc/cluster-ai/`), la base (`/var/lib/cluster-ai/`), el lanzador ni la regla de sudo: solo el código en `/opt/cluster-ai`.
- No editar archivos directamente en `/opt/cluster-ai` (por ejemplo `config/cluster.yaml`): el próximo `git pull` daría conflicto. Hacer el cambio en el repositorio, subirlo y actualizar.
- Si `git pull` se queja de cambios locales, revisar qué cambió con `git status` y `git diff` antes de descartar nada.
