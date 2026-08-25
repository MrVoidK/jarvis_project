"""Proje koku tabanli mutlak yollar.

Araclar (tools/notes.py, tools/files.py) once goreli yollar ("notes", "jarvis_workspace")
kullaniyordu; guvenlik incelemesi (security-reviewer, Faz 3) bunun process'in CWD'sine
bagimli oldugunu, Jarvis bir servis/zamanlanmis gorev olarak farkli bir dizinden
baslatilirsa beklenmedik bir konumda dosya olusturabilecegini not etti. Yollar artik
bu modulden, kaynak agacindan turetilmis PROJECT_ROOT uzerinden veriliyor.
"""

import os

# src/jarvis/core/paths.py -> src/jarvis/core -> src/jarvis -> src -> <proje koku>
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, os.pardir)
)
