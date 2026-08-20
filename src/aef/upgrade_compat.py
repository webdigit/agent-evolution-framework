"""Single compatibility source for workspace UPGRADE. Do not duplicate these."""

from __future__ import annotations

from ._version import __version__

TARGET_WORKSPACE_SCHEMA_VERSION = "1.0.0"
SUPPORTED_PROFILE = "aef-v1"
MAX_MIGRATIONS_PER_PLAN = 100
MAX_MANAGED_PATHS = 256
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TRANSACTION_BYTES = 50 * 1024 * 1024
MAX_JSON_DEPTH = 64
UPGRADE_TRANSACTION_PATH = ".agent/state/upgrade-transaction.json"
LEDGER_PATH = ".agent/state/migrations.json"
MANIFEST_PATH = ".agent/manifest.json"


def installed_package_version() -> str:
    return __version__


def production_migrations() -> tuple:
    """Public registry. Empty while the productive target stays 1.0.0."""
    return ()


def managed_paths() -> frozenset[str]:
    return frozenset()


def ordered_migrations() -> tuple:
    return production_migrations()
