import importlib.metadata
from arcus.provider_runtime.version import __version__


def test_runtime_version_matches_package_metadata():
    assert __version__ == importlib.metadata.version("arcus-provider-runtime")
