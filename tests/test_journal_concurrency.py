from concurrent.futures import ThreadPoolExecutor

from lorekeep.journal import append_journal, load_journals, update_journal_status
from lorekeep.models import JournalEntry


def _entry(index: int, proposed_at: str = "2026-07-29T00:00:00Z") -> JournalEntry:
    return JournalEntry(
        entry_id=f"entry-{index}",
        fact={
            "kind": "node",
            "id": f"svc:{index}",
            "type": "service",
            "ns": ["backend"],
            "props": {},
            "src": [],
        },
        agent="test",
        device="test-device",
        ns="backend",
        confidence=0.9,
        proposed_at=proposed_at,
        status="pending",
    )


def test_concurrent_appends_do_not_lose_or_corrupt_lines(tmp_path):
    entries = [_entry(i) for i in range(50)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda entry: append_journal(
            tmp_path, entry, "backend",
        ), entries))

    loaded = load_journals(tmp_path)
    assert {entry.entry_id for entry in loaded} == {
        f"entry-{i}" for i in range(50)
    }


def test_status_update_uses_entry_id_not_second_precision_timestamp(tmp_path):
    append_journal(tmp_path, _entry(1), "backend")
    append_journal(tmp_path, _entry(2), "backend")

    update_journal_status(tmp_path, "backend", {"entry-1"}, "merged")

    by_id = {entry.entry_id: entry.status for entry in load_journals(tmp_path)}
    assert by_id == {"entry-1": "merged", "entry-2": "pending"}
