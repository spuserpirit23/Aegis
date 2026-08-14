import os
import subprocess
import sys

if __name__ == "__main__":
    py_exe = r"C:\Users\ramak\AppData\Local\Python\pythoncore-3.11-64\python.exe"
    if not os.path.exists(py_exe):
        py_exe = sys.executable
    subprocess.run([py_exe, "main.py"], check=False)
