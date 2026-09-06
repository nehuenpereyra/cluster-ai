Este es un desafío clásico de seguridad en servidores compartidos Linux/HPC: **si el comando corre directamente bajo la cuenta del investigador, cualquier archivo que el comando pueda leer o escribir, el investigador también podría leerlo (la API Key) o modificarlo (la DB)**.

Para resolver esto sin exponer credenciales ni permitir la alteración manual de `cluster.db`, existen **dos soluciones estándar en entornos Linux**. Te presento la más simple y recomendada, y la alternativa más avanzada:

---

### Solución Recomendada: Usuario de Servicio del Sistema + `sudo` NOPASSWD (Sin daemons)

Esta es la forma más limpia en Debian porque **no requiere dejar nada corriendo de fondo**.

#### 1. ¿Cómo funciona la arquitectura?
1. Se crea un usuario de sistema en Debian llamado `cluster-ai` (sin contraseña ni shell de acceso directo).
2. La carpeta `/opt/cluster-ai`, el archivo `.env` y la base de datos `cluster.db` pertenecen **únicamente** a este usuario con permisos restrictivos (`chmod 600` para el `.env` y `chmod 700` para la carpeta). **Ningún investigador puede hacerles `cat`, ni abrirlos con `sqlite3`, ni borrarlos**.
3. Se autoriza a todos los usuarios a ejecutar **únicamente** ese comando como el usuario `cluster-ai` sin pedir contraseña mediante una regla en `sudoers`.
4. El ejecutable detecta automáticamente la variable del sistema **`$SUDO_USER`**, que Linux fija de forma segura e infalsificable con el usuario real de la sesión SSH.

---

#### 2. Paso a paso de cómo se configura en Debian:

**Paso A: Crear el usuario de servicio del sistema**
```bash
sudo useradd -r -s /bin/false -d /opt/cluster-ai cluster-ai
```

**Paso B: Proteger los archivos y la base de datos**
```bash
sudo chown -R cluster-ai:cluster-ai /opt/cluster-ai
# Solo cluster-ai puede leer y escribir (privacidad total para .env y cluster.db)
sudo chmod 700 /opt/cluster-ai
sudo chmod 600 /opt/cluster-ai/.env
sudo chmod 600 /opt/cluster-ai/cluster.db
```

**Paso C: Crear la regla de ejecución en `/etc/sudoers.d/cluster-ai`**
Crea el archivo `/etc/sudoers.d/cluster-ai`:
```bash
sudo bash -c 'cat << "EOF" > /etc/sudoers.d/cluster-ai
ALL ALL=(cluster-ai) NOPASSWD: /opt/cluster-ai/.venv/bin/cluster-ai
ALL ALL=(cluster-ai) NOPASSWD: /opt/cluster-ai/.venv/bin/cai
EOF'
sudo chmod 440 /etc/sudoers.d/cluster-ai
```

**Paso D: Crear el script envoltorio transparente en `/usr/local/bin/cluster-ai`**
Para que los investigadores no tengan que escribir `sudo`, se coloca un script que lo hace de forma invisible para ellos:
```bash
sudo bash -c 'cat << "EOF" > /usr/local/bin/cluster-ai
#!/bin/bash
exec sudo -u cluster-ai /opt/cluster-ai/.venv/bin/cluster-ai "$@"
EOF'

sudo chmod +x /usr/local/bin/cluster-ai
sudo ln -sf /usr/local/bin/cluster-ai /usr/local/bin/cai
```

---

### ¿Cómo sabe `cluster-ai` qué usuario de Linux lo ejecutó?

Cuando un comando corre mediante `sudo -u cluster-ai`, el kernel de Linux inyecta automáticamente la variable de entorno:
```python
import os
import getpass

# 1. Si se invoca con sudo, SUDO_USER contiene el usuario real de Linux (ej: 'npereyra')
# 2. Si se invoca directamente, se usa getpass.getuser() o os.getlogin()
usuario_real = os.getenv("SUDO_USER") or getpass.getuser()
```
- El investigador no puede falsear `$SUDO_USER` porque `sudo` la sobrescribe con la identidad de la sesión SSH activa.
- Al registrar en la base de datos, cada corrida queda asociada inequívocamente a ese usuario.

---

### 🛡️ ¿Qué beneficios de seguridad obtienes con este esquema?

| Requisito | Estado | Cómo se garantiza |
| :--- | :---: | :--- |
| **API Key oculta** | ✅ Protegida | El `.env` tiene permisos `600` de `cluster-ai`. Un investigador que haga `cat /opt/cluster-ai/.env` recibe *Permission Denied*. |
| **Integridad de `cluster.db`** | ✅ Inmutable | Ningún usuario puede borrar la base, ni modificar métricas, ni alterar el historial con SQLite. Solo el script añade registros. |
| **Trazabilidad por usuario** | ✅ Verídica | Se extrae directamente de `$SUDO_USER` provisto por el sistema operativo. |
| **Simplicidad de uso** | ✅ Transparente | El usuario solo escribe `cluster-ai diagnose ...` como cualquier comando nativo, sin claves ni pasos extra. |

---

### Alternativa: Servicio Local con UNIX Domain Socket (Daemon)
Si en el futuro quisieras que `cluster-ai` maneje colas internas o cachés en memoria, la alternativa es crear un demonio en `systemd` que escuche en `/run/cluster-ai.sock`. En ese caso:
- El daemon corre como `cluster-ai` manteniendo la DB y la API Key.
- El CLI solo envía la petición al socket.
- El socket usa `SO_PEERCRED` del kernel de Linux para obtener el UID exacto de quien envía el mensaje.

Sin embargo, para la etapa actual, **la solución con `sudo -u cluster-ai` es mucho más ligera, no consume memoria de fondo y resuelve exactamente todos los puntos que necesitas.**