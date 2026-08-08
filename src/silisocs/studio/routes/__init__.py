"""Per-area FastAPI routers for Silisocs Studio.

Every module here imports FastAPI at module scope, so they are imported from
inside ``create_app`` — after the guard that turns a missing ``silisocs[studio]``
extra into a readable error.
"""
