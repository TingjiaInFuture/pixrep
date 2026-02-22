from __future__ import annotations


def ruff_severity(code: str) -> str:
    if code.startswith(("F", "E", "B")):
        return "high"
    if code.startswith(("SIM", "PLR", "W", "RUF", "C90")):
        return "medium"
    return "medium"


def iter_target_batches(
    targets: list[str],
    *,
    max_batch_items: int = 200,
    max_batch_chars: int = 60_000,
):
    if not targets:
        return

    batch: list[str] = []
    cur_chars = 0
    for target in targets:
        target_len = len(target) + 1
        if batch and (len(batch) >= max_batch_items or (cur_chars + target_len) > max_batch_chars):
            yield batch
            batch = []
            cur_chars = 0
        batch.append(target)
        cur_chars += target_len

    if batch:
        yield batch
