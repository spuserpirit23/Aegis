import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    project_dir = Path(__file__).resolve().parent
    venv_python = project_dir / ".venv" / "Scripts" / "python.exe"
    py_exe = str(venv_python) if venv_python.exists() else sys.executable
    subprocess.run([py_exe, str(project_dir / "main.py")], cwd=project_dir, check=False)
