import pytest

from brain.audit import AuditLog, set_audit_log


@pytest.fixture(autouse=True)
def isolated_audit_log(tmp_path):
    """Keep tests from writing to the real logs/audit.jsonl."""
    log = AuditLog(tmp_path / "audit.jsonl")
    set_audit_log(log)
    yield log
    set_audit_log(None)
