"""Test support for this context: its eval graders (`eval_graders`).

Never imported by the API or the worker. Like `dw_platform.testing`, it may
import what only the dev workspace installs (`dw_evals`), which is why
`verify_architecture.py` exempts `testing` packages from the declared
dependency check.
"""
