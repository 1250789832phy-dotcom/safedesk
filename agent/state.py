from agent.models import TaskStatus


ALLOWED_TRANSITIONS = {
    TaskStatus.RECEIVED: {
        TaskStatus.CLARIFYING,
        TaskStatus.PLANNED,
        TaskStatus.FAILED,
        TaskStatus.BLOCKED,
    },
    TaskStatus.PLANNED: {
        TaskStatus.RUNNING,
        TaskStatus.BLOCKED,
        TaskStatus.FAILED,
    },
    TaskStatus.RUNNING: {
        TaskStatus.VERIFYING,
        TaskStatus.BLOCKED,
        TaskStatus.FAILED,
    },
    TaskStatus.VERIFYING: {
        TaskStatus.SUCCEEDED,
        TaskStatus.BLOCKED,
        TaskStatus.FAILED,
    },
}


def transition(current: TaskStatus, target: TaskStatus) -> TaskStatus:
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise ValueError(f"Illegal task state transition: {current} -> {target}")
    return target
