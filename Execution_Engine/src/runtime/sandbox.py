"""RestrictedPython sandbox — compile + execute user code safely.

Never use raw eval()/exec(). All user code passes through
compile_restricted() which performs AST inspection and blocks
dangerous constructs (import, open, __import__, os, sys, etc.).

Callers must run this in a separate thread (asyncio.to_thread) to
avoid blocking the event loop, and wrap with asyncio.wait_for for
timeout enforcement.
"""
from __future__ import annotations

from RestrictedPython import compile_restricted, safe_builtins
from RestrictedPython.Guards import (
    guarded_unpack_sequence,
    safer_getattr,
)
from RestrictedPython.Eval import default_guarded_getiter


def _default_getitem(obj, key):
    return obj[key]


def _default_write(obj):
    return obj


import operator as _op

_INPLACE_OPS = {
    "+=": _op.iadd, "-=": _op.isub, "*=": _op.imul,
    "/=": _op.itruediv, "//=": _op.ifloordiv, "%=": _op.imod,
    "**=": _op.ipow, "&=": _op.iand, "|=": _op.ior, "^=": _op.ixor,
}


def _inplacevar(op_name, x, y):
    return _INPLACE_OPS[op_name](x, y)


def run_restricted(
    code: str,
    inputs: dict,
    *,
    timeout_seconds: int = 30,
) -> dict:
    byte_code = compile_restricted(code, "<user_code>", "exec")
    result: dict = {}
    safe_globals = {
        "__builtins__": {
            **safe_builtins,
            "_getiter_": default_guarded_getiter,
            "_getattr_": safer_getattr,
            "_getitem_": _default_getitem,
            "_write_": _default_write,
            "_inplacevar_": _inplacevar,
            "_unpack_sequence_": guarded_unpack_sequence,
            "_iter_unpack_sequence_": guarded_unpack_sequence,
        },
        "inputs": dict(inputs),
        "result": result,
    }
    exec(byte_code, safe_globals)  # noqa: S102 — safe: byte_code is compile_restricted output
    return result
