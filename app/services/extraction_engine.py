"""
Extraction Engine — Extracts values from API responses into variables.
Extracted from flow_executor_service.py for modularity and testability.
"""
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


def resolve_json_path(data: Any, path: str) -> Optional[Any]:
    """Traverses a JSON object or array supporting dot notation, brackets ([0]), and root ($)."""
    if not path:
        return data
    path_str = str(path).strip()
    if path_str == '$':
        return data
    if path_str.startswith('$.'):
        path_str = path_str[2:]
    elif path_str.startswith('$'):
        path_str = path_str[1:]
        
    path_str = re.sub(r'\[(\d+)\]', r'.\1', path_str)
    path_str = re.sub(r'\[[\'"]([^\'"]+)[\'"]\]', r'.\1', path_str)
    parts = [p for p in path_str.split('.') if p != '']

    curr = data
    for p in parts:
        if isinstance(curr, dict) and p in curr:
            curr = curr[p]
        elif isinstance(curr, list):
            try:
                curr = curr[int(p)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return curr


def extract_value(rule: dict, resp_headers: dict, resp_json: Any) -> tuple:
    """
    Extracts a value from the response based on an extraction rule.
    
    Args:
        rule: dict with keys 'source', 'property', 'variable'
        resp_headers: response headers dict
        resp_json: parsed response JSON (or None)
    
    Returns:
        (variable_name: str, extracted_value: str | None)
    """
    r_source = rule.get('source', 'body')
    r_prop = rule.get('property', '')
    r_var = rule.get('variable', '').strip().upper()

    val = None
    if r_source == 'header':
        h_key = r_prop.lower()
        val = next((v for k, v in resp_headers.items() if k.lower() == h_key), None)
    elif r_source == 'body' and resp_json:
        val = resolve_json_path(resp_json, r_prop)

    if val is not None and r_var:
        return r_var, str(val)
    return r_var, None


def process_extractions(rules: list, resp_headers: dict, resp_json: Any,
                        variables_dict: dict, lock=None,
                        db=None, product_id: int = None, env_id: int = None) -> dict:
    """
    Processes a list of extraction rules against a response.
    
    Updates variables_dict in-place (thread-safe if lock provided).
    Optionally persists to DB via VariableService.
    
    Returns dict of newly extracted {var_name: var_value}.
    """
    extracted = {}

    for rule in rules:
        try:
            var_name, var_value = extract_value(rule, resp_headers, resp_json)
            if var_value is not None and var_name:
                if lock:
                    with lock:
                        variables_dict[var_name] = var_value
                else:
                    variables_dict[var_name] = var_value

                extracted[var_name] = var_value
                logger.info(f"      ✅ Extracted [{var_name}] = '{var_value}'")

                # Persist to DB if context is available (env_id can be None for global variables)
                if db is not None and product_id is not None:
                    try:
                        from app.services.variable_service import VariableService
                        from app.schemas.variable_schemas import VariableCreate
                        new_var = VariableCreate(
                            name=var_name, value=var_value,
                            project_id=product_id, environment_id=env_id,
                            type="extracted"
                        )
                        VariableService.create(db, new_var)
                    except Exception as db_err:
                        logger.warning(f"      ⚠️ Could not persist extracted var to DB: {db_err}")
        except Exception as ee:
            logger.error(f"        ❌ Extraction Error: {ee}")

    return extracted
