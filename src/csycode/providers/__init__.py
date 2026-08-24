# Import provider modules to trigger registration
# Use try/except to allow partial imports during development
try:
    from csycode.providers import anthropic  # noqa: F401
except ImportError:
    pass

try:
    from csycode.providers import openai  # noqa: F401
except ImportError:
    pass
