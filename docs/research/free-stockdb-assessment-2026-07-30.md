# free-stockdb Assessment

Source: https://github.com/hello245m/free-stockdb (reviewed 2026-07-30).

## Decision

Do not install or depend on free-stockdb in AQSP production now.

The upstream project is MIT licensed and its local-first design is relevant,
but its latest release is Windows-only. Its documented minimum memory is 2GB,
above the production host's 1.6GB. Adding a C++ service or an HTTP/MCP bridge
would also expand the runtime surface before its Linux build, source provenance,
and point-in-time data semantics have been verified.

## Principles Adopted

- Keep data synchronization separate from research reads.
- Use bounded, resumable market-universe chunks instead of per-symbol parallel
  fetches.
- Keep raw bars and point-in-time adjustment factors distinct from display data.
- Push indicator calculation to bounded local batches only when its output can
  preserve the existing no-look-ahead contract.
- Treat an external local data engine as a `DataSource` implementation behind
  AQSP's typed interface, never as a replacement for ledger or strategy rules.

## Admission Gate

Reconsider only after all conditions are met:

1. A reproducible Linux release runs within the server resource gate.
2. The data source, timestamps, raw prices, adjustment factors, suspensions,
   and price-limit fields pass AQSP's `DataSource` contract tests.
3. A benchmark against the current SQLite path demonstrates lower wall-clock
   time without raising peak memory or weakening daily coverage checks.
4. The integration remains optional and does not expose a new public service.
