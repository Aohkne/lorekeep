import logging

from lorekeep.journal import load_journals


def test_corrupt_journal_line_warns_without_logging_content(tmp_path, caplog):
    path = tmp_path / "pending" / "private" / "journal.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"secret":"raw-private-content"}\nnot-json-secret-content\n')
    with caplog.at_level(logging.WARNING, logger="lorekeep.journal"):
        assert load_journals(tmp_path / "pending") == []
    assert caplog.text.count("invalid journal line") == 2
    assert "line=1" in caplog.text and "line=2" in caplog.text
    assert "raw-private-content" not in caplog.text
    assert "not-json-secret-content" not in caplog.text
