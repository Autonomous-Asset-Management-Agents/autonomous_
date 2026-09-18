# RPAR parity golden fixtures

These JSON files are **golden snapshots of the running bundle engine's**
`SpecialistReport` DTO. They are the reference the Dev-Env engine is graded
against by `tests/parity/harness.py::compare_reports` as the staged
report-parity port (RPAR Epic #1262) lands task by task.

## File format

```jsonc
{
  "_meta": {
    "status": "real",                       // "real" | "SYNTHETIC EXAMPLE …"
    "bundle_sha": "<git sha or build id>",   // REQUIRED for real fixtures
    "bundle_captured_at": "<ISO-8601 UTC>",  // REQUIRED for real fixtures
    "symbol": "AAPL",
    "schema": "SpecialistReport DTO (…_serialize_specialist_report)"
  },
  "report": { /* the serialized SpecialistReport DTO */ }
}
```

`example_AAPL.golden.json` is a **synthetic** example: it documents the format
and exercises the comparator's loader, but it is **not** a real capture
(`bundle_sha: null`). Do not grade parity against it.

## Why frozen fixtures (not a live dual-engine run)?

Per the V0 design (Architekturfrage 1, Option B): CI must be deterministic and
reproducible. The bundle is not a git repo and needs network + an LLM to run,
so a live diff is neither pinnable nor deterministic. Freezing the bundle's
output once — with the bundle's git-SHA/build-id recorded in `_meta` — gives a
versioned, reproducible oracle. The cost is a deliberate **refresh** step.

## Refresh runbook (capturing real golden fixtures)

> Requires access to the bundle checkout (`AI Trading Bot/`), which is **not**
> part of this repository. The bundle anchors in the V0 plan are verified
> against an external bundle checkout only.

1. Check out the target bundle build and record its SHA / build-id.
2. For each parity symbol (start with a 3–5 symbol basket, e.g. AAPL, MSFT,
   NVDA, JPM, XOM), run the bundle specialist with **the same mocked inputs**
   you will feed the Dev-Env engine in the parity test (same news, same data
   sources, same model availability), and capture its serialized report DTO.
3. Write `<SYMBOL>.golden.json` with `_meta.status = "real"`,
   `_meta.bundle_sha`, and `_meta.bundle_captured_at` set.
4. Commit the fixtures. The capture must be reproducible from step 1's SHA.

## When to refresh

Refresh when the bundle's report schema or generation logic changes in a way
that should become the new Dev-Env parity target. Always bump `bundle_sha`.
A stale fixture makes parity tests assert against an outdated oracle.
