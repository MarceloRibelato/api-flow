"""
Assertion Engine — Evaluates API response assertions.
Extracted from flow_executor_service.py for modularity and testability.
"""
import logging
import re
import json
from typing import Any, Optional

logger = logging.getLogger(__name__)


def evaluate_assertion(assertion: dict, resp_status: int, resp_headers: dict,
                       resp_json: Any, resp_text: str, duration: int) -> dict:
    """
    Evaluates a single assertion rule against a response.
    
    Returns a dict with:
      source, operator, target, actual, success
    """
    src_raw = assertion.get('source') or assertion.get('type') or 'statusCode'
    src = 'statusCode' if src_raw in ['status', 'statusCode'] else src_raw

    raw_operator = assertion.get('operator', 'equals')
    op = str(raw_operator).lower()

    target = assertion.get('value') if assertion.get('value') is not None else assertion.get('target')
    prop = assertion.get('property') or assertion.get('path')

    actual_val = None
    is_success = False

    try:
        if src == 'statusCode':
            actual_val = resp_status
            try:
                t_int = int(str(target).strip())
                is_success = _compare_numeric(actual_val, t_int, op)
            except (ValueError, TypeError):
                is_success = False

        elif src in ['header', 'headers']:
            h_key = str(prop).lower() if prop else ""
            actual_val = next((v for k, v in resp_headers.items() if k.lower() == h_key), None)
            str_act = str(actual_val) if actual_val is not None else ""
            str_tar = str(target)
            is_success = _compare_string(str_act, str_tar, op, actual_val)

        elif src == 'responseTime':
            actual_val = duration
            try:
                target_clean = ''.join(c for c in str(target) if c.isdigit() or c == '.')
                t_int = int(float(target_clean)) if target_clean else 0
                is_success = _compare_numeric(actual_val, t_int, op)
            except (ValueError, TypeError):
                is_success = False

        elif src == 'body':
            is_success, actual_val = _evaluate_body_assertion(op, prop, target, resp_json)

        elif src == 'contract':
            schema_target = target
            if schema_target and resp_json is not None:
                try:
                    import jsonschema
                    if isinstance(schema_target, str):
                        try:
                            schema_dict = json.loads(schema_target)
                        except Exception:
                            schema_dict = None
                    elif isinstance(schema_target, dict):
                        schema_dict = schema_target
                    else:
                        schema_dict = None

                    if schema_dict and isinstance(schema_dict, dict) and (
                        'type' in schema_dict or 'properties' in schema_dict or '$schema' in schema_dict or 'required' in schema_dict
                    ):
                        try:
                            jsonschema.validate(instance=resp_json, schema=schema_dict)
                            is_success = resp_status < 400
                            actual_val = "Schema Match" if is_success else f"HTTP Status {resp_status}"
                        except jsonschema.ValidationError as ve:
                            is_success = False
                            actual_val = f"Schema Mismatch: {ve.message}"
                        except jsonschema.SchemaError as se:
                            is_success = False
                            actual_val = f"Invalid JSON Schema: {se.message}"
                    else:
                        is_success = resp_status < 400
                        actual_val = "Schema Match" if is_success else "Invalid"
                except ImportError:
                    is_success = resp_status < 400
                    actual_val = "Schema Match" if is_success else "Invalid"
            else:
                is_success = resp_status < 400
                actual_val = "Schema Match" if is_success else "Invalid"

    except Exception as ae:
        logger.error(f"Assertion evaluation error: {ae}")
        is_success = False

    return {
        "source": src_raw,
        "operator": raw_operator,
        "property": str(prop) if prop else None,
        "target": str(target),
        "actual": str(actual_val),
        "success": is_success
    }


def evaluate_all_assertions(assertions: list, resp_status: int, resp_headers: dict,
                            resp_json: Any, resp_text: str, duration: int) -> tuple:
    """
    Evaluates a list of assertions. Returns (results_list, all_passed).
    """
    results = []
    all_passed = True

    for assertion in assertions:
        result = evaluate_assertion(assertion, resp_status, resp_headers, resp_json, resp_text, duration)
        results.append(result)
        if not result['success']:
            all_passed = False

    return results, all_passed


# ── Private Helpers ──────────────────────────────────────────────────────


def _compare_numeric(actual: int, target: int, op: str) -> bool:
    """Compares two numeric values based on operator string."""
    if op in ['equals', 'eq', '==', 'is']:
        return actual == target
    elif op in ['notequals', 'neq', '!=']:
        return actual != target
    elif op in ['gt', 'greaterthan', '>']:
        return actual > target
    elif op in ['lt', 'lessthan', '<']:
        return actual < target
    elif op in ['gte', '>=']:
        return actual >= target
    elif op in ['lte', '<=']:
        return actual <= target
    return False


def _compare_string(actual: str, target: str, op: str, raw_actual: Any = None) -> bool:
    """Compares string values based on operator string, matching frontend capabilities."""
    op_lower = op.lower() if op else ""
    
    if op_lower in ['equals', 'eq', 'is', '==']:
        return actual == target or str(actual).strip().lower() == str(target).strip().lower()
    elif op_lower in ['notequals', 'neq', '!=', 'not_equals']:
        return actual != target
    elif op_lower in ['contains', 'in']:
        return target in actual
    elif op_lower in ['notcontains', 'not_contains', 'not_in']:
        return target not in actual
    elif op_lower in ['exists', 'not_null']:
        return raw_actual is not None
    elif op_lower in ['notexists', 'not_exists', 'is_null']:
        return raw_actual is None
    elif op_lower == 'istype':
        if raw_actual is None: return str(target).lower() == 'null'
        if str(target).lower() == 'array': return isinstance(raw_actual, list)
        if str(target).lower() == 'object': return isinstance(raw_actual, dict)
        if str(target).lower() in ['number', 'integer', 'float']: return isinstance(raw_actual, (int, float))
        if str(target).lower() == 'boolean': return isinstance(raw_actual, bool)
        if str(target).lower() == 'string': return isinstance(raw_actual, str)
        return False
        
    return False


def _resolve_json_path(data: Any, path: str) -> Optional[Any]:
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


def _evaluate_body_assertion(op: str, prop: str, target: Any, resp_json: Any) -> tuple:
    """
    Evaluates a body assertion. Returns (is_success, actual_value).
    """
    op_lower = str(op).lower()
    
    if op_lower in ['exists', 'not_null']:
        if not prop:
            is_success = resp_json is not None
            actual_val = "Body Received" if is_success else None
        else:
            actual_val = _resolve_json_path(resp_json, prop)
            is_success = actual_val is not None
            actual_val = str(actual_val) if is_success else None
        return is_success, actual_val
        
    if op_lower in ['notexists', 'not_exists', 'is_null']:
        if not prop:
            is_success = resp_json is None
            actual_val = None
        else:
            actual_val = _resolve_json_path(resp_json, prop)
            is_success = actual_val is None
        return is_success, str(actual_val) if actual_val is not None else None

    if resp_json is None:
        return False, None

    actual_val = _resolve_json_path(resp_json, prop)
    str_act = str(actual_val) if actual_val is not None else ""
    str_tar = str(target)

    is_success = False
    
    if op_lower in ['equals', 'eq', 'is', '==']:
        # Try direct type matching first, then string
        if type(actual_val) == type(target) and actual_val == target:
            is_success = True
        else:
            is_success = str_act == str_tar or str_act.strip() == str_tar.strip()
    elif op_lower in ['notequals', 'neq', '!=', 'not_equals']:
        is_success = str_act != str_tar
    elif op_lower in ['contains', 'in']:
        is_success = str_tar in str_act
    elif op_lower in ['notcontains', 'not_contains', 'not_in']:
        is_success = str_tar not in str_act
    elif op_lower in ['greaterthan', 'gt', '>']:
        try:
            is_success = float(actual_val) > float(target)
        except (ValueError, TypeError): pass
    elif op_lower in ['lessthan', 'lt', '<']:
        try:
            is_success = float(actual_val) < float(target)
        except (ValueError, TypeError): pass
    elif op_lower == 'istype':
        if actual_val is None: is_success = str(target).lower() == 'null'
        elif str(target).lower() == 'array': is_success = isinstance(actual_val, list)
        elif str(target).lower() == 'object': is_success = isinstance(actual_val, dict)
        elif str(target).lower() in ['number', 'integer', 'float']: is_success = isinstance(actual_val, (int, float))
        elif str(target).lower() == 'boolean': is_success = isinstance(actual_val, bool)
        elif str(target).lower() == 'string': is_success = isinstance(actual_val, str)

    return is_success, actual_val
