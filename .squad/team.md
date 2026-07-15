# Squad Team

> forge

## Coordinator

| Name | Role | Notes |
|------|------|-------|
| Squad | Coordinator | Routes work, enforces handoffs and reviewer gates. |

## Members

| Name | Role | Charter | Status |
|------|------|---------|--------|
| Ripley | Lead / Architect | `.squad/agents/ripley/charter.md` | active |
| Bishop | Python Core Dev | `.squad/agents/bishop/charter.md` | active |
| Hicks | Tester | `.squad/agents/hicks/charter.md` | active |
| Lambert | Integration Engineer | `.squad/agents/lambert/charter.md` | active |
| Scribe | Session Logger | `.squad/agents/scribe/charter.md` | active |
| Ralph | Work Monitor | — | active |


## Coding Agent

<!-- copilot-auto-assign: false -->

| Name | Role | Charter | Status |
|------|------|---------|--------|
| @copilot | Coding Agent | — | 🤖 Coding Agent |

### Capabilities

**🟢 Good fit — auto-route when enabled:**
- Bug fixes with clear reproduction steps
- Test coverage (adding missing tests, fixing flaky tests)
- Lint/format fixes and code style cleanup
- Dependency updates and version bumps
- Small isolated features with clear specs
- Boilerplate/scaffolding generation
- Documentation fixes and README updates

**🟡 Needs review — route to @copilot but flag for squad member PR review:**
- Medium features with clear specs and acceptance criteria
- Refactoring with existing test coverage
- API endpoint additions following established patterns
- Migration scripts with well-defined schemas

**🔴 Not suitable — route to squad member instead:**
- Architecture decisions and system design
- Multi-system integration requiring coordination
- Ambiguous requirements needing clarification
- Security-critical changes (auth, encryption, access control)
- Performance-critical paths requiring benchmarking
- Changes requiring cross-team discussion

## Project Context

- **Project:** forge
- **Created:** 2026-07-02
- **User:** briandenicola
- **Description:** Python regulatory impact framework for assessing regulatory change impact and scoring compliance using deterministic offline engines, Fabric-ready data, and future Microsoft Agent Framework / Foundry Hosted Agent integration.
- **Current focus:** Portable deterministic Regulation Interpreter core tasks 1-4.
