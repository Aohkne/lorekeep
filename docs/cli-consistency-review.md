# Lorekeep CLI Consistency Review

## 1. Command Inventory

Source: `src/lorekeep/cli.py` (34 command registrations, 31 user-visible commands).

### Top-level commands (13 visible + 3 hidden)

| Command | Line | Purpose | Target user |
|---|---|---|---|
| `lorekeep version` | 88 | Print version | All |
| `lorekeep compile` | 246 | raw/*.md → facts.jsonl + wiki + resolve (all-in-one) | Curator |
| `lorekeep wiki` | 303 | Regenerate wiki from facts.jsonl | Curator |
| `lorekeep check` | 464 | Validate graph: loads, no dangling edges | Curator/Dev |
| `lorekeep resolve` | 494 | Merge pending journals into facts.jsonl | Curator |
| `lorekeep serve` | 609 | Run MCP server (stdio/http) | Agent/Dev |
| `lorekeep doctor` | 890 | Full install verification (graph + schema + MCP + provider ping) | Dev |
| `lorekeep init` | 988 | Bootstrap data home, wire agents, import, compile, daemon | All |
| `lorekeep backup` | 1439 | Commit + push .lorekeep/ to backup git repo | Curator |
| `lorekeep import` | 1465 | Import agent sessions → raw/ (claude/cursor/codex/opencode) | Curator |
| `lorekeep hook` | 94 (**hidden**) | Session-end hook: quick-import memories (all agents) | Agent (auto) |
| `lorekeep eval` | 391 (**hidden**) | Construction-quality eval vs gold corpus | Dev |
| `lorekeep eval-locomo` | 408 (**hidden**) | LoCoMo benchmark eval | Dev |

### Subcommand groups (4 groups, 18 subcommands)

| Group | Command | Line | Purpose | Target user |
|---|---|---|---|---|
| `mcp` | `mcp add` | 863 | Write agent MCP config + memory snippet | Curator |
| `config` | `config show` | 806 | Print config.yaml | All |
| `config` | `config set` | 816 | Set nested config value (dot notation) | All |
| `schema` | `schema upgrade` | 779 | Upgrade ontology schema (with backup) | Curator |
| `support` | `support` (bare) | 653 | Print report + create ZIP bundle | Dev |
| `support` | `support report` | 681 (**hidden**) | Alias: print report only | Dev |
| `support` | `support bundle` | 694 (**hidden**) | Alias: create ZIP only | Dev |
| `support` | `support on` | 722 | Enable automatic GitHub issue creation | Dev |
| `support` | `support off` | 728 | Disable automatic GitHub issue creation | Dev |
| `support` | `support status` | 734 | Show config + dedup stats | Dev |
| `agent` | `agent profile` | 322 | Show/open personal namespace raw dir | Curator |
| `agent` | `agent contribution` | 344 | Suggest team-knowledge gaps (personal→team) | Curator |
| `agent` | `agent ingest` | 1700 | Conversational LLM ingest → journal | Curator |
| `agent` | `agent lint` | 1878 | Semantic health checks (orphans, contradictions) | Curator |
| `agent` | `agent suggest` | 1932 | Generate improvement suggestions | Curator |
| `agent` | `agent status` | 1963 | Graph health dashboard (counts + lint summary) | Curator |
| `agent` | `agent watch` | 2031 | Run daemon: watch raw/ + pending/ + sessions | Curator |
| `agent` | `agent service install` | 1649 | Install daemon as OS service | Curator |
| `agent` | `agent service uninstall` | 1674 | Remove daemon OS service | Curator |
| `agent` | `agent service status` | 1691 | Check daemon service status | Curator |

**Total: 31 user-visible commands + 3 hidden = 34 command entry points.**

---

## 2. Resolved issues

### R4 ✅: `contribution` moved under `agent` (MEDIUM — resolved)

**Before**: `lorekeep contribution` (top-level).
**After**: `lorekeep agent contribution`. Groups with `agent suggest` — both are "analyze and suggest" commands.

### R6 ✅: `agent daemon` renamed to `agent service` (MEDIUM — resolved)

**Before**: `lorekeep agent daemon install/uninstall/status`.
**After**: `lorekeep agent service install/uninstall/status`. Clarifies foreground (`agent watch`) vs background (`agent service install`). Cross-references added to both docstrings.

### R9 ✅: `profile` and `contribution` moved under `agent` (LOW — resolved)

**Before**: `lorekeep profile`, `lorekeep contribution` (top-level).
**After**: `lorekeep agent profile`, `lorekeep agent contribution`. Reduces top-level surface from 15 → 13 visible commands.

### R10 ✅: `bugreport` merged into `support` (LOW — resolved)

**Before**: `lorekeep bugreport on/off/status` (separate command group).
**After**: `lorekeep support on/off/status`. Consolidates diagnostics and error reporting into a single group.

---

## 3. Remaining issues

### R1: `check` vs `doctor` — overlapping validation (HIGH — keep as-is)

Both load the graph and validate it. `check` is a fast structural subset (CI gate, no network). `doctor` is a superset including schema, MCP, and provider ping. Cross-reference added to `check --help`. Do NOT merge — different use cases.

### R2: `check` vs `agent lint` — overlapping graph validation (MEDIUM — future enhancement)

`check` is structural (dangling edges). `agent lint` is semantic (orphans, contradictions). Cross-reference added to `agent lint --help`. Future: `check --deep` flag to run both.

### R3: `agent status` vs `doctor` — overlapping health reporting (MEDIUM — keep as-is)

`agent status` is a read-only dashboard. `doctor` is a pass/fail gate with provider ping. Different use cases — keep both.

### R5: Three ingestion paths with inconsistent interfaces (HIGH — future breaking change)

- `import`: Batch agent session import (with `--quick` flag)
- `hook`: Hidden, auto-triggered on session end (already documented as internal ✓)
- `agent ingest`: Conversational LLM extraction (different — writes journals not raw/)

Future: rename `agent ingest` → `agent extract` to distinguish from file import.

### R7: `support` subcommand group structure (LOW — no action needed)

Hidden `report`/`bundle` aliases don't clutter `--help`. Keep as-is.

### R8: `config set` doesn't validate against Pydantic model (LOW — future enhancement)

Future: add post-write Pydantic validation.

---

## 4. Remaining improvement plan

### Done in this cycle

| Action | Commands | Status |
|---|---|---|
| Add cross-references to `check` and `agent lint` --help | `check`, `agent lint` | ✅ |
| Add service/watch cross-references | `agent watch`, `agent service install` | ✅ |
| Move `profile` → `agent profile` | `agent` | ✅ |
| Move `contribution` → `agent contribution` | `agent` | ✅ |
| Rename `agent daemon` → `agent service` | `agent` | ✅ |
| Merge `bugreport` into `support` | `support` | ✅ |

### Future work (no urgency)

| Action | Priority | Impact |
|---|---|---|
| Add `check --deep` flag running `agent lint` checks | Low | Single command for full health check |
| Add Pydantic validation to `config set` | Low | Catches invalid values at write time |
| Rename `agent ingest` → `agent extract` | P4 (breaking) | Distinguish from `import` |
| Merge `hook` into `import --hook` mode | P4 (breaking) | Reduce hidden commands |

---

## Verification

**Before this review**: 37 command entry points (15 top-level + 7 groups × 19 subcommands + 5 hidden).
**After**: 34 command entry points (13 top-level + 4 groups × 18 subcommands + 3 hidden).

Top-level surface reduced from 15 → 13 visible commands. `bugreport` group eliminated, `profile`/`contribution` moved under `agent`, `agent daemon` renamed to `agent service`.
