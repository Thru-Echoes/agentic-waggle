"""Demo + operator scripts for Waggle.

Public entry points:
    * `scripts.demo.run()` — 5-minute end-to-end walkthrough that picks an
      unread inbound email, runs the LangGraph extraction agent, transitions
      the resulting order through its FSM, logs an ERP sync call, and asks
      the Sales Copilot for a follow-up.
"""
