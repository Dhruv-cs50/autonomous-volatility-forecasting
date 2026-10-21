"""Features: trailing realized measures, HAR components, and the forward targets.

Two halves with opposite relationships to time, kept apart on purpose.

``realized`` and ``har`` look only **backwards**. They are pure functions over a return series,
never see a date, and compute from a trailing window or nothing at all — an incomplete window
yields null rather than a partial sum. ``tests/test_point_in_time.py`` enforces that
structurally with an AST scan over this package, so a forward offset here is a build failure
rather than a judgement call.

``targets`` looks **forwards**, because a target is a forward quantity by definition. It is the
single exemption from that scan, and it is safe for a different reason: each target row is
stamped with the session on which its horizon completes, so the store's own point-in-time filter
refuses to hand back a target that has not yet realised. The guard does that work, not
discipline.
"""
