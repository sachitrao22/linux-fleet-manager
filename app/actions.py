"""
Built-in, read-only actions the agent implements itself (in Python, with psutil) —
no shell involved, so they behave the same on Linux, macOS and Windows and can't be
abused to run arbitrary code.

A command whose text starts with "@" is one of these. Agents started with --safe (the
public-demo installer always does) refuse anything else, and the server only lets
demo visitors send these. Keep this list in sync with ACTIONS in agent/agent.py.
"""
ACTIONS = {
    "@uptime": "Uptime",
    "@os": "System info",
    "@memory": "Memory",
    "@disk": "Disk usage",
    "@top": "Top processes",
    "@network": "Network",
}


def is_action(command: str) -> bool:
    return command.strip() in ACTIONS
