from importlib.metadata import PackageNotFoundError, version

PACKAGE_NAME = "gitcopilot"

try:
    __version__ = version(PACKAGE_NAME)
except PackageNotFoundError:
    # Fallback for running directly from source without installation
    __version__ = "0.0.0+local"