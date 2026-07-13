# autonomous-audit-mcp

**An MCP server that lets an AI trading agent record, verify, and report its own decisions to a
tamper-evident audit log — as native, agent-callable tools.**

Thin [Model Context Protocol](https://modelcontextprotocol.io) wrapper around the open-source
[`autonomous-audit`](https://pypi.org/project/autonomous-audit/) tool (Apache-2.0, Python standard
library only). No new audit logic — it exposes the existing hash-chain over stdio so an agent in
Claude Desktop, Cursor, or any MCP client can build a tamper-evident decision trail *while it
works*, without writing code.

## Tools

| Tool | What it does |
|---|---|
| `record_decision(log_path, decision)` | Append one decision (`symbol`, `side`, `qty`, `reason`, `timestamp`, …) to a SHA-256 hash-chained log. Returns the new hash + record count. |
| `verify_log(log_path)` | Full-chain integrity check. Detects in-place edits (hash mismatch) and deletions/reorders (prev_hash break). |
| `export_report(log_path, output_path?)` | Render a self-contained HTML report (integrity banner + one row per decision + disclosure). |
| `chain_head(log_path)` | Return the current head hash + record count — the value to publish/sign **externally** so truncation or a genesis rewrite (which the local chain alone cannot catch) becomes detectable. |

Read-only with respect to any broker or market — it never places, modifies, or cancels orders; it
only appends to the local audit log you point it at.

## Run

```bash
uvx autonomous-audit-mcp        # runs the MCP server over stdio
```

Register it with an MCP client, e.g. Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "autonomous-audit": { "command": "uvx", "args": ["autonomous-audit-mcp"] }
  }
}
```

## Scope & disclosure

Provides **tamper-evidence** (integrity + traceability): it detects post-hoc edits, reorders, and
mid-chain deletions. It is **tamper-evident, not tamper-proof durable storage**, does not on its
own satisfy statutory record-keeping (e.g. MiFID II), and is neither model explainability nor a
regulatory approval. For educational and informational purposes only — **not** investment advice.
See the [`autonomous-audit` scope & regulatory context](https://pypi.org/project/autonomous-audit/).

## License

Apache-2.0.
