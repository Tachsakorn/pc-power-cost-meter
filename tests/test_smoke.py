from importlib import metadata

import pytest

import power_meter


def test_package_imports() -> None:
    assert power_meter.__version__ == "0.2.0"


def test_installed_metadata_matches_package_version() -> None:
    try:
        installed = metadata.version("power-meter")
    except metadata.PackageNotFoundError:
        pytest.skip("power-meter is not installed")
    assert installed == power_meter.__version__
