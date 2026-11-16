"""Configure the bundled Node runtime for this process before importing ExecJS."""
import os
from pathlib import Path


def configure_runtime():
    directory = Path(__file__).parent / "runtime"
    if (directory / "node.exe").is_file():
        os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
        os.environ["EXECJS_RUNTIME"] = "Node"
