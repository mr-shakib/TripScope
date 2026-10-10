"""The API image has no pipeline extras: importing the app must not need Spark or the geo libraries."""

import subprocess
import sys

PIPELINE_ONLY = ("pyspark", "shapefile", "pyproj", "shapely")


def test_api_imports_without_pipeline_extras() -> None:
    # A None entry in sys.modules makes any import of that package (or its submodules) raise ImportError.
    blocked = "; ".join(f"sys.modules[{name!r}] = None" for name in PIPELINE_ONLY)
    code = f"import sys; {blocked}; import tripscope.api.app"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)  # noqa: S603
    assert result.returncode == 0, result.stderr[-2000:]
