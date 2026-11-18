"""shared/identity.py - Resolución confiable de la identidad del usuario del sistema operativo."""

import getpass
import os
from typing import Optional, Set


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


def current_groups() -> Optional[Set[str]]:
    """
    Nombres de los grupos del proceso actual (primario y suplementarios), o None si no se
    pueden determinar (Windows). Se usa para saber si el investigador puede usar una cola
    restringida por AllowGroups.
    """
    if not hasattr(os, "getgroups"):
        return None
    try:
        import grp

        names = set()
        for gid in set(os.getgroups()) | {os.getgid()}:
            try:
                names.add(grp.getgrgid(gid).gr_name)
            except KeyError:
                continue
        return names
    except ImportError:
        return None
