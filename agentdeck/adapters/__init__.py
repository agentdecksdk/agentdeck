"""Ring 2 — one directory per external system, each behind a core port.

Nothing outside an adapter directory may import that system, and no adapter imports
another: deleting one must break nothing but itself.
"""
