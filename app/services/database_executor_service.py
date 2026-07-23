import json
import logging
import time
from typing import Dict, Any, Tuple, List
from sqlalchemy import create_engine, text
from app.services.variable_resolver import replace_vars as _replace_vars

logger = logging.getLogger(__name__)

class DatabaseExecutorService:
    @staticmethod
    def execute_db_step(step_data: Dict[str, Any], variables_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes a SQL query against a database configured via connection string.
        Supports variable substitution, result extractions, and assertions.
        """
        start_time = time.time()
        
        # Extract properties (support flat structure or properties sub-object)
        props = step_data.get('properties', {}) if isinstance(step_data.get('properties'), dict) else {}
        
        raw_conn_str = step_data.get('connection_string') or step_data.get('connectionString') or props.get('connection_string') or props.get('connectionString') or ""
        raw_query = step_data.get('query') or step_data.get('sql') or props.get('query') or props.get('sql') or ""
        timeout = int(step_data.get('timeout') or props.get('timeout') or 10)
        
        # Resolve variables in connection string and SQL query
        conn_str = _replace_vars(raw_conn_str, variables_dict).strip()
        sql_query = _replace_vars(raw_query, variables_dict).strip()
        
        assertions_raw = step_data.get('assertions') or props.get('assertions') or []
        extracts_raw = step_data.get('extracts') or props.get('extracts') or []
        
        if isinstance(assertions_raw, str):
            try: assertions_raw = json.loads(assertions_raw)
            except: assertions_raw = []
            
        if isinstance(extracts_raw, str):
            try: extracts_raw = json.loads(extracts_raw)
            except: extracts_raw = []

        if not conn_str:
            return {
                'status': 400,
                'reason': 'Missing Connection String',
                'text': 'A connection string de banco de dados não foi informada.',
                'duration': 0,
                'error': 'Missing Connection String',
                'extracted': {},
                'assertions': [],
                'assertions_passed': False
            }

        if not sql_query:
            return {
                'status': 400,
                'reason': 'Missing SQL Query',
                'text': 'A consulta SQL não foi informada.',
                'duration': 0,
                'error': 'Missing SQL Query',
                'extracted': {},
                'assertions': [],
                'assertions_passed': False
            }

        # Fix connection string driver format for SQLite or PostgreSQL if short format used
        if conn_str.startswith("postgres://"):
            conn_str = conn_str.replace("postgres://", "postgresql://", 1)

        engine = None
        try:
            connect_args = {}
            if "sqlite" in conn_str:
                connect_args = {"timeout": timeout}
            elif "postgresql" in conn_str or "postgres" in conn_str:
                connect_args = {"connect_timeout": timeout}
            elif "mysql" in conn_str:
                connect_args = {"connect_timeout": timeout}

            engine = create_engine(
                conn_str,
                pool_pre_ping=True,
                pool_timeout=timeout,
                connect_args=connect_args
            )

            with engine.connect() as connection:
                is_read_query = sql_query.lstrip().upper().startswith(("SELECT", "WITH", "SHOW", "EXPLAIN"))
                
                if is_read_query:
                    result = connection.execute(text(sql_query))
                    # Convert rows to list of dicts (up to 500 rows limit)
                    keys = result.keys()
                    rows_raw = result.fetchmany(500)
                    
                    rows = []
                    for row in rows_raw:
                        row_dict = {}
                        for idx, key in enumerate(keys):
                            val = row[idx]
                            # Serialize non-JSON primitive types if needed
                            if hasattr(val, 'isoformat'):
                                val = val.isoformat()
                            row_dict[key] = val
                        rows.append(row_dict)
                    
                    row_count = len(rows)
                    resp_text = json.dumps(rows, default=str)
                else:
                    result = connection.execute(text(sql_query))
                    connection.commit()
                    row_count = result.rowcount if hasattr(result, 'rowcount') else 0
                    rows = [{"affected_rows": row_count}]
                    resp_text = json.dumps({"status": "Executed", "affected_rows": row_count})

            duration = int((time.time() - start_time) * 1000)
            
            # --- Extractions ---
            extracted_vars = {}
            first_row = rows[0] if rows else {}
            
            for ext in extracts_raw:
                if not isinstance(ext, dict): continue
                var_name = ext.get('variable') or ext.get('var_name')
                prop = ext.get('property') or ext.get('field') or ext.get('column')
                source = ext.get('source') or 'column'
                
                if not var_name: continue
                
                if source == 'rowCount' or prop == 'rowCount':
                    extracted_vars[var_name] = row_count
                elif source == 'json' or prop == 'json' or prop == 'rows':
                    extracted_vars[var_name] = resp_text
                elif prop and prop in first_row:
                    extracted_vars[var_name] = first_row[prop]
                elif prop and len(rows) > 0:
                    # Case insensitive column match
                    matched_key = next((k for k in first_row.keys() if k.lower() == str(prop).lower()), None)
                    if matched_key:
                        extracted_vars[var_name] = first_row[matched_key]
                    else:
                        extracted_vars[var_name] = None
                else:
                    extracted_vars[var_name] = None

            # --- Assertions ---
            assertion_results = []
            assertions_passed = True
            
            for ass in assertions_raw:
                if not isinstance(ass, dict): continue
                src = ass.get('source') or ass.get('type') or 'rowCount'
                prop = ass.get('property') or ass.get('column') or ass.get('field')
                op = ass.get('operator') or 'equals'
                target_val = ass.get('target') if ass.get('target') is not None else ass.get('value')
                
                # Resolve variables in target_val if string
                if isinstance(target_val, str):
                    target_val = _replace_vars(target_val, variables_dict)
                
                actual_val = None
                if src == 'rowCount' or prop == 'rowCount':
                    actual_val = row_count
                elif src == 'column' or prop:
                    if first_row and prop:
                        matched_key = next((k for k in first_row.keys() if k.lower() == str(prop).lower()), None)
                        actual_val = first_row.get(matched_key) if matched_key else None
                    else:
                        actual_val = None
                else:
                    actual_val = resp_text
                    
                passed = False
                try:
                    if op == 'equals':
                        passed = str(actual_val) == str(target_val)
                    elif op == 'not_equals':
                        passed = str(actual_val) != str(target_val)
                    elif op == 'greater_than':
                        passed = float(actual_val) > float(target_val)
                    elif op == 'less_than':
                        passed = float(actual_val) < float(target_val)
                    elif op == 'contains':
                        passed = str(target_val) in str(actual_val)
                    elif op == 'not_empty':
                        passed = actual_val is not None and str(actual_val).strip() != "" and actual_val != []
                    elif op == 'is_null':
                        passed = actual_val is None
                    else:
                        passed = str(actual_val) == str(target_val)
                except Exception as eval_err:
                    logger.warning(f"Error evaluating assertion: {eval_err}")
                    passed = False

                if not passed:
                    assertions_passed = False
                    
                assertion_results.append({
                    'source': src,
                    'property': prop,
                    'operator': op,
                    'target': target_val,
                    'actual': actual_val,
                    'passed': passed
                })

            return {
                'status': 200,
                'reason': 'OK',
                'text': resp_text,
                'duration': duration,
                'error': None if assertions_passed else 'DB Assertions Failed',
                'extracted': extracted_vars,
                'assertions': assertion_results,
                'assertions_passed': assertions_passed
            }

        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            err_msg = str(e)
            logger.error(f"❌ [DatabaseExecutorService] Query Execution Failed: {err_msg}")
            return {
                'status': 500,
                'reason': 'Database Error',
                'text': err_msg,
                'duration': duration,
                'error': err_msg,
                'extracted': {},
                'assertions': [],
                'assertions_passed': False
            }
        finally:
            if engine:
                engine.dispose()
