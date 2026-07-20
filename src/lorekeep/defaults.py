"""Default config + schema used by `lorekeep init` to bootstrap a fresh home."""
from __future__ import annotations

DEFAULT_SCHEMA = {
    # Ontology v2 (schema_version 3): work-context types bridging personal (me)
    # and team namespaces. Catch-all types (concept/tool/command/note) removed —
    # tokens that used to become those nodes are now attributes (see the altitude
    # rule in compile/extract.py). Validation is type-name only; from/to below are
    # guidance for the extractor, not a hard gate.
    "version": 3,
    "node_types": {
        # people / subject
        "person": {"props": {"name": "string", "handle": "string", "role": "string", "org": "string"}},
        "role": {"props": {"name": "string", "domain": "string"}},
        "skill": {"props": {"name": "string", "domain": "string", "level": "string"}},
        # knowledge
        "domain": {"props": {"name": "string", "description": "string"}},
        "preference": {"props": {"name": "string", "description": "string"}},
        "value": {"props": {"name": "string", "description": "string"}},
        "goal": {"props": {"title": "string", "timeframe": "string", "status": "string"}},
        # team / work
        "service": {"props": {"name": "string", "lang": "string", "owner": "string", "status": "string"}},
        "project": {"props": {"name": "string", "status": "string", "start_date": "string"}},
        "decision": {"props": {"title": "string", "status": "string", "decided_on": "string"}},
        "team": {"props": {"name": "string", "org": "string"}},
        "document": {"props": {"title": "string", "kind": "string"}},
    },
    "edge_types": {
        # entity-centric (team)
        "depends_on": {"from": "service", "to": "service"},
        "part_of": {"from": "service", "to": "project"},
        "decided_by": {"from": "decision", "to": "person"},
        # cross-ns bridge (subject -> team)
        "owns": {"from": "person", "to": "service"},
        "contributes_to": {"from": "person", "to": "project"},
        "works_on": {"from": "person", "to": "project"},
        # subject knowledge
        "skilled_in": {"from": "person", "to": "domain"},
        "is_a": {"from": "service", "to": "domain"},
        "collaborates_with": {"from": "person", "to": "person"},
        "prefers": {"from": "person", "to": "preference"},
        # generic / doc
        "relates_to": {"from": "service", "to": "service"},
        "documents": {"from": "document", "to": "service"},
    },
}

DEFAULT_CONFIG_YAML = """\
provider:
  model: openai/gpt-4o-mini
  api_base: null
  api_key_env: OPENAI_API_KEY
  api_key: null
  temperature: 0.0
compile:
  chunk_lines: 60
ns:
  default: [public]
install_source: pypi
"""

SAMPLE_DOC = """\
# Lorekeep sample

This file demonstrates how raw markdown becomes a knowledge graph.
Delete it once you add your own docs.

## Services

**api-gateway** is the main entry point, written in Go.

**auth-service** handles authentication, written in Python.

The api-gateway depends on auth-service for token validation.

## Decisions

ADR-001: Adopt api-gateway pattern for all client traffic.
This was decided by the backend team.
"""
