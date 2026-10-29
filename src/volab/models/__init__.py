"""The model zoo: contestants for the walk-forward, each behind ``volab.models.base``.

This file imports nothing, and that is the point. ``volab.walkforward`` imports
``volab.models.base`` and nothing else from this package, so a contestant that needs ``arch`` or
``scipy`` can live here without ``import volab.walkforward`` ever pulling them in. Importing the
package must stay free for the same reason. Witness:
``test_importing_the_walkforward_does_not_import_arch_or_scipy``.
"""
