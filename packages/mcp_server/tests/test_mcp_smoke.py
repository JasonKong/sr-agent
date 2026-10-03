from importlib.metadata import version

import sr_mcp


def test_package_version_matches_installed_metadata() -> None:
    assert sr_mcp.__version__ == version("sr-mcp")
