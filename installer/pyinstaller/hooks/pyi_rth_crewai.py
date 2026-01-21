"""
Runtime hook for crewai package.

CrewAI uses __file__ based path resolution to find translation files,
which doesn't work correctly with PyInstaller. This hook patches the
path resolution to use the correct bundled path.
"""

import os
import sys


def _get_crewai_translations_path():
    """Get the correct path to crewai translations in bundled app."""
    if getattr(sys, 'frozen', False):
        # Running in PyInstaller bundle
        base_path = sys._MEIPASS
        return os.path.join(base_path, 'crewai', 'translations')
    else:
        # Running normally
        return None


# Patch crewai's i18n module to find translations correctly
_translations_path = _get_crewai_translations_path()
if _translations_path:
    os.environ.setdefault('CREWAI_TRANSLATIONS_PATH', _translations_path)
