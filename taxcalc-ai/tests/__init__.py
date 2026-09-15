"""Test package for taxcalc-ai.

Made a real package (rather than a bare directory of modules) so that test modules can import
shared constants from ``tests.conftest`` explicitly, instead of relying on pytest's
``sys.path`` insertion to make a bare ``conftest`` importable.
"""
