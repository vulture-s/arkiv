"""APIRouter modules for the arkiv server (R5-25 / round-5 #51).

The #51 finding: server.py had grown into a ~4.5k-line monolith mixing route
handlers, business logic, and helpers. The router split peels the route groups
into focused APIRouter modules that server.py mounts via app.include_router().

The prerequisite leaf service modules (pathres / webguard / export_builders /
reqopts, plus the pre-existing auth / admin / db / state / settings modules) hold
the cross-group helpers, so a router module imports what it needs directly — no
`from server import ...`, no router→server→router import cycle.
"""
