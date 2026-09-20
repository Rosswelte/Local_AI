from dataclasses import dataclass


@dataclass(frozen=True)
class JobPolicy:
    timeout_s: int
    max_retries: int


POLICIES = {
    "text": JobPolicy(timeout_s=300, max_retries=2),
    "pull": JobPolicy(timeout_s=120, max_retries=2),
    "image": JobPolicy(timeout_s=1800, max_retries=1),
    "stt": JobPolicy(timeout_s=600, max_retries=1),
    "tts": JobPolicy(timeout_s=120, max_retries=1),
    "agent": JobPolicy(timeout_s=1800, max_retries=1),
}


class Scheduler:
    def __init__(self, worker_count: int = 1):
        self.worker_count = max(1, worker_count)

    def policy(self, job: dict) -> JobPolicy:
        default = POLICIES.get(job.get("kind", "text"), JobPolicy(timeout_s=300, max_retries=0))
        return JobPolicy(
            timeout_s=int(job.get("timeout_s") or default.timeout_s),
            max_retries=int(job.get("max_retries") if job.get("max_retries") is not None else default.max_retries),
        )

    @staticmethod
    def retry_delay(attempt: int) -> int:
        return min(30, 2 ** max(0, attempt - 1))

    @staticmethod
    def can_retry(code: str | None, retryable: bool = True) -> bool:
        return retryable and code in {"provider_error", "provider_unreachable", "provider_timeout"}
