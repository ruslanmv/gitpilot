"""GitPilot package."""

from . import loopback as _loopback
from .version import __version__

# Before any client opens a connection: localhost resolves to IPv4 first, so a
# local service running in WSL answers at once instead of after a 21-second
# IPv6 connect timeout (see gitpilot/loopback.py).
_loopback.install()

__all__ = ["__version__"]
