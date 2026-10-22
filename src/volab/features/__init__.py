"""Features: trailing realized measures, HAR components, and the forward targets.

Two halves with opposite relationships to time, kept apart on purpose.

``realized`` and ``har`` look only **backwards**. They are pure functions over a return series,
never see a date, and compute from a trailing window or nothing at all — an incomplete window
yields null rather than a partial sum.

The defence for that is the perturbation suite in ``tests/test_point_in_time.py``, which changes
data after a cutoff and requires the table at the cutoff to come back byte-identical. The AST
scan in the same file is a cheap ratchet beside it, **not** a defence: it fails on the
constructs it enumerates — a ``shift``/``diff`` offset that is not a non-negative integer
literal, a ``bfill``/``backfill``/``interpolate``, a ``method=`` other than
``ffill``/``pad``/``None``, a non-false ``center=``, any slice — and is blind to every forward
peek spelled another way.
Four such evasions are measured and recorded in ``docs/superpowers/phase1-decisions.md``. The
perturbations catch more, but not everything either: a predicate constant across the
perturbation images, and cross-call state, defeat them both, and both classes are open.

``targets`` looks **forwards**, because a target is a forward quantity by definition. It is the
single exemption from that scan, and it is safe for a different reason: each target row is
stamped with the session on which its horizon completes, so the store's own point-in-time filter
refuses to hand back a target that has not yet realised. The guard does that work, not
discipline.
"""
