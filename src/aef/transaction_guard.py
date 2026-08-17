from .evaluation_transaction import TRANSACTION_PATH


def evaluation_recovery_required(project):
    """Return true when project mutations must wait for explicit recovery."""
    files = project.get("files") if isinstance(project, dict) else None
    return isinstance(files, dict) and TRANSACTION_PATH in files


def mutation_guard_metadata(project):
    if not evaluation_recovery_required(project):
        return None
    return {
        "reason": "evaluation_recovery_required",
        "transaction_path": TRANSACTION_PATH,
    }
