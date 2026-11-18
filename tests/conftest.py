"""tests/conftest.py - Configuración común de la suite de pruebas."""

import os

# La suite se ejecuta siempre en modo desarrollo (con el emulador disponible), aunque se
# corra en el servidor donde el helper instalado haría detectar producción. Los tests que
# verifican el modo producción fijan CLUSTER_AI_ENV explícitamente.
os.environ["CLUSTER_AI_ENV"] = "development"
