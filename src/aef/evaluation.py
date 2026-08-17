from datetime import datetime, timedelta


def review_due(policy, *, tasks_since_review=0, last_review_at=None, now=None, incident=False):
    if incident:
        return True
    mode = policy["mode"]
    if mode == "manual":
        return False
    if mode in {"task_count", "adaptive"} and policy.get("every_tasks") is not None:
        if tasks_since_review >= policy["every_tasks"]:
            return True
    if mode in {"interval", "adaptive"} and policy.get("interval_days") is not None:
        if last_review_at is None:
            return True
        now = now or datetime.now()
        last = datetime.fromisoformat(last_review_at)
        if now >= last + timedelta(days=policy["interval_days"]):
            return True
    return False
