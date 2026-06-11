"""Pluggable sandbox providers for the GitPilot repair flow.

.. note::

   The top-level :mod:`gitpilot.sandbox` *module* already exists for an
   unrelated local-sandbox feature, so this provider abstraction lives in
   ``gitpilot.sandbox_providers`` to avoid a package/module name clash while
   still matching the documented ``sandbox/base.py`` +
   ``sandbox/matrixlab_client.py`` contract.
"""

from .base import SandboxProvider, SandboxResult
from .matrixlab_client import MatrixLabClient

__all__ = ["SandboxProvider", "SandboxResult", "MatrixLabClient"]
