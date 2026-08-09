"""Insurance for the registry's dotted-string indirection.

Because AgentSpec references its writer, importer, and their functions by name
rather than by import, a typo would only surface at runtime on whichever
machine happens to have that agent installed. These tests resolve every name
at test time instead.
"""
import pytest

from lorekeep.integrations import registry


def test_agent_names_are_the_supported_agents():
    assert registry.AGENT_NAMES == (
        "claude", "codex", "cursor", "opencode", "grok", "qoder",
    )


def test_supported_agents_alias_matches_registry():
    from lorekeep.integrations.detect import SUPPORTED_AGENTS
    assert set(SUPPORTED_AGENTS) == set(registry.AGENT_NAMES)


@pytest.mark.parametrize("spec", registry.all_specs(), ids=lambda s: s.name)
def test_writer_module_imports(spec):
    writer = spec.writer()
    assert callable(writer.write_config)


@pytest.mark.parametrize("spec", registry.all_specs(), ids=lambda s: s.name)
def test_supports_hook_matches_reality(spec):
    """`supports_hook` replaced a hasattr() probe; it must not drift from it."""
    assert spec.supports_hook == hasattr(spec.writer(), "write_hook")


@pytest.mark.parametrize("spec", registry.all_specs(), ids=lambda s: s.name)
def test_importer_module_imports_when_present(spec):
    if not spec.importer_module:
        pytest.skip(f"{spec.name}: no importer yet")
    assert spec.importer() is not None


@pytest.mark.parametrize("spec", registry.all_specs(), ids=lambda s: s.name)
def test_every_declared_importer_attr_exists(spec):
    if not spec.importer_module:
        pytest.skip(f"{spec.name}: no importer yet")
    importer = spec.importer()
    names = []
    if spec.memory:
        names += [spec.memory.dir_finder, spec.memory.import_fn]
    if spec.session:
        names += [
            spec.session.locate, spec.session.parse,
            spec.session.key, spec.session.dump_fn,
        ]
        if spec.session.deep_fn:
            names.append(spec.session.deep_fn)
    for name in names:
        assert callable(getattr(importer, name, None)), f"{spec.name}: {name}"


@pytest.mark.parametrize("spec", registry.all_specs(), ids=lambda s: s.name)
def test_memory_source_implies_namespace(spec):
    assert (spec.memory is None) == (spec.memory_ns is None)
    assert (spec.session is None) or (spec.session_ns is not None)


def test_namespaces_are_unique():
    namespaces = [
        ns for s in registry.all_specs()
        for ns in (s.memory_ns, s.session_ns) if ns
    ]
    assert len(namespaces) == len(set(namespaces))


@pytest.mark.parametrize("spec", registry.all_specs(), ids=lambda s: s.name)
def test_wiring_targets_are_declared(spec):
    assert spec.user_config and spec.user_config.startswith("~/")
    if spec.project_config:  # some agents are user-scope only (grok)
        assert not spec.project_config.startswith(("~", "/"))
    assert spec.supports_hook == bool(spec.project_hook)
    assert spec.supports_hook == bool(spec.user_hook)


@pytest.mark.parametrize("spec", registry.all_specs(), ids=lambda s: s.name)
def test_session_handle_kind_is_known(spec):
    if spec.session:
        assert spec.session.handle_kind in ("dir", "file", "id", "blob")


def test_every_importing_agent_has_a_session_source():
    """Agents with importers must have at least a session path."""
    importing = [s for s in registry.all_specs() if s.importer_module]
    assert all(s.session is not None for s in importing)


def test_find_and_get():
    assert registry.find("claude").label == "Claude Code"
    assert registry.find("nope") is None
    assert registry.get("codex").name == "codex"
    with pytest.raises(KeyError):
        registry.get("nope")


def test_specs_are_immutable():
    spec = registry.get("claude")
    with pytest.raises(Exception):
        spec.name = "other"
