"""Model Context Protocol servers for Waggle.

Two servers are shipped, both built on `fastmcp`:
    * `sales_copilot_server` — customer / order / customer-report lookups +
      follow-up-email drafting. The LLM-facing surface for the sales team.
    * `customer_reports_server` — narrative extraction, disease / varroa queries,
      fleet-health aggregation.

Each server runs as a standalone process (stdio transport by default; SSE
is available via fastmcp's `server.run("sse")` call). The app itself does
not import these modules — they're a sibling process.
"""
