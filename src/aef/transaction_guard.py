from .evaluation_transaction import TRANSACTION_PATH
from .upgrade_compat import UPGRADE_TRANSACTION_PATH
from .upgrade_transaction import upgrade_transaction_present


def evaluation_recovery_required(project):
    """Return true when project mutations must wait for explicit recovery."""
    files = project.get("files") if isinstance(project, dict) else None
    return isinstance(files, dict) and TRANSACTION_PATH in files


def upgrade_recovery_required(project):
    """Return true when UPGRADE recovery must run before other mutations."""
    return upgrade_transaction_present(project)


def mutation_guard_metadata(project):
    if evaluation_recovery_required(project):
        return {
            "reason": "evaluation_recovery_required",
            "transaction_path": TRANSACTION_PATH,
        }
    if upgrade_recovery_required(project):
        return {
            "reason": "upgrade_recovery_required",
            "transaction_path": UPGRADE_TRANSACTION_PATH,
        }
    return None
