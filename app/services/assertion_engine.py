"""
Assertion Engine — Evaluates API response assertions.
Extracted from flow_executor_service.py for modularity and testability.
"""
import logging
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

        elif src == 'header':
            h_key = str(prop).lower() if prop else ""
            actual_val = next((v for k, v in resp_headers.items() if k.lower() == h_key), None)
            str_act = str(actual_val) if actual_val is not None else ""
            str_tar = str(target)
            is_success = _compare_string(str_act, str_tar, op, actual_val)

        elif src == 'responseTime':
            actual_val = duration
            try:
                t_int = int(str(target).strip())
                is_success = _compare_numeric(actual_val, t_int, op)
            except (ValueError, TypeError):
                is_success = False

        elif src == 'body':
            is_success, actual_val = _evaluate_body_assertion(op, prop, target, resp_json)

        elif src == 'contract':
            is_success = resp_status < 400
            actual_val = "Schema Match" if is_success else "Invalid"

    except Exception as ae:
        logger.error(f"Assertion evaluation error: {ae}")
        is_success = False

    return {
        "source": src_raw,
        "operator": raw_operator,
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
    """Compares string values based on operator."""
    if op in ['equals', 'eq', 'is']:
        return actual == target
    elif op in ['contains', 'in']:
        return target in actual
    elif op in ['exists', 'not_null']:
        return raw_actual is not None
    return False


def _resolve_json_path(data: Any, path: str) -> Optional[Any]:
    """Traverses a JSON object by dot-separated path."""
    if not path:
        return data
    parts = str(path).split('.')
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
    if op == 'exists':
        if not prop:
            is_success = resp_json is not None
            actual_val = "Body Received" if is_success else None
        else:
            actual_val = _resolve_json_path(resp_json, prop)
            is_success = actual_val is not None
            actual_val = str(actual_val) if is_success else None
        return is_success, actual_val

    if resp_json is None:
        return False, None

    actual_val = _resolve_json_path(resp_json, prop)
    str_act = str(actual_val) if actual_val is not None else ""
    str_tar = str(target)

    is_success = False
    if op in ['equals', 'eq', 'is']:
        is_success = str_act == str_tar
    elif op in ['contains', 'in']:
        is_success = str_tar in str_act
    elif op in ['exists', 'not_null']:
        is_success = actual_val is not None

    return is_success, actual_val
