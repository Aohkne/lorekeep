from pathlib import Path
from lorekeep.schema_io import load_schema


def test_load_schema_from_file(tmp_path: Path):
    p = tmp_path / "schema.json"
    p.write_text('{"version":1,"node_types":{"service":{"props":{"name":"string"}}},'
                 '"edge_types":{"depends_on":{"from":"service","to":"service"}}}')
    s = load_schema(p)
    assert s.version == 1
    assert s.is_valid_node_type("service")
    assert not s.is_valid_node_type("nope")
