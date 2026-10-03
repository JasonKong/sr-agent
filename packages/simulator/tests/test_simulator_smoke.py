from importlib.metadata import version

import sr_simulator


def test_package_version_matches_installed_metadata() -> None:
    assert sr_simulator.__version__ == version("sr-simulator")
