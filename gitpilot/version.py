from importlib.metadata import PackageNotFoundError, version

PACKAGE_NAME = "gitcopilot"

try:
    __version__ = version(PACKAGE_NAME)
except PackageNotFoundError:
    __version__ = "0.0.0+local"
