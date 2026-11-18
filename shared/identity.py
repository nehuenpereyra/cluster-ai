"""shared/identity.py - Resolución confiable de la identidad del usuario del sistema operativo."""

import getpass
import os


def current_user() -> str:
    """
    Devuelve el nombre del usuario dueño del proceso actual.

    En POSIX se resuelve a partir del UID real (`os.getuid()`) contra la base de usuarios
    del sistema, por lo que no puede falsearse modificando variables de entorno como
    `USER`, `LOGNAME` o `USERNAME`. En Windows (solo desarrollo) se usa `getpass`.
    """
    if hasattr(os, "getuid"):
        try:
            import pwd

            return pwd.getpwuid(os.getuid()).pw_name
        except (ImportError, KeyError):
            pass
    try:
        return getpass.getuser()
    except Exception:
        return "investigador"
