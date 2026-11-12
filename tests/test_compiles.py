"""Every module under src/ must compile: a syntax error in a module no test
imports (src/inseason/cli.py) otherwise only shows up when the Docker
build runs compileall."""
import compileall
from pathlib import Path


def test_all_of_src_compiles():
    src = Path(__file__).resolve().parents[1] / "src"
    assert compileall.compile_dir(str(src), quiet=1, force=True)
