"""
Variable Resolver — Handles {{variable}} substitution in flow execution.
Extracted from flow_executor_service.py for modularity and testability.
"""
import re
import logging

logger = logging.getLogger(__name__)


def replace_vars(text: str, variables: dict) -> str:
    """
    Replaces {{VARIABLE_NAME}} placeholders in text with values from the variables dict.
    
    The variables dict can contain:
      - Direct string values: {"VAR_NAME": "value"}
      - ORM objects with .name and .value attributes
    
    Matching is case-insensitive (normalized to UPPER).
    """
    if not text or not isinstance(text, str):
        return text

    def replacer(match):
        var_name = match.group(1).strip().upper()

        # 1. Direct Lookup (fastest path)
        if var_name in variables:
            val = variables[var_name]
            if hasattr(val, 'value'):
                return str(val.value)
            return str(val)

        # 2. Fallback: scan ORM objects by .name attribute
        for v in variables.values():
            if hasattr(v, 'name') and v.name.upper() == var_name:
                logger.debug(f"Replaced '{match.group(1)}' with '{v.value}' (Fallback Match)")
                return str(v.value)

        # 3. Not found — keep original placeholder
        logger.warning(f"Variable '{var_name}' NOT FOUND. Available: {list(variables.keys())}")
        return match.group(0)

    return re.sub(r'\{\{([\w\.\-_]+)\}\}', replacer, text)
