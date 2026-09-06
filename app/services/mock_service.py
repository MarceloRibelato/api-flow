import asyncio
import json
import re
import uuid
import random
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple, List
from sqlalchemy.orm import Session

from app.models.mock_models import ServiceMockDB, MockRuleDB, MockLogDB


class MockExecutionEngine:

    @staticmethod
    def apply_date_offset(dt: datetime, offset_str: str) -> datetime:
        if not offset_str:
            return dt
        m = re.match(r"^([+-])(\d+)\s*([dhmy])$", offset_str.strip().lower())
        if not m:
            return dt
        sign, qty, unit = m.groups()
        val = int(qty) if sign == "+" else -int(qty)
        if unit == "d":
            return dt + timedelta(days=val)
        elif unit == "h":
            return dt + timedelta(hours=val)
        elif unit == "m":
            return dt + timedelta(minutes=val)
        elif unit == "y":
            return dt + timedelta(days=val * 365)
        return dt

    @staticmethod
    def format_datetime_custom(dt: datetime, fmt: str) -> str:
        if not fmt:
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        fmt_clean = fmt.strip()
        fmt_lower = fmt_clean.lower()

        # Presets rápidos comuns
        if fmt_lower in ["iso", "iso8601"]:
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        elif fmt_lower in ["iso_ms", "iso8601_ms"]:
            ms = f"{int(dt.microsecond / 1000):03d}"
            return dt.strftime(f"%Y-%m-%dT%H:%M:%S.{ms}Z")
        elif fmt_lower in ["br", "dmy"]:
            return dt.strftime("%d/%m/%Y")
        elif fmt_lower in ["br_datetime", "br_time", "dmy_hms"]:
            return dt.strftime("%d/%m/%Y %H:%M:%S")
        elif fmt_lower in ["br_short"]:
            return dt.strftime("%d/%m/%y")
        elif fmt_lower in ["ymd", "date"]:
            return dt.strftime("%Y-%m-%d")
        elif fmt_lower in ["ymd_hms", "datetime"]:
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        elif fmt_lower in ["time", "his"]:
            return dt.strftime("%H:%M:%S")
        elif fmt_lower in ["time_12h"]:
            return dt.strftime("%I:%M:%S %p")
        elif fmt_lower in ["epoch", "unix", "timestamp"]:
            return str(int(dt.timestamp()))
        elif fmt_lower in ["epoch_ms", "timestamp_ms"]:
            return str(int(dt.timestamp() * 1000))
        elif fmt_lower in ["compact", "ymd_compact"]:
            return dt.strftime("%Y%m%d")
        elif fmt_lower in ["compact_datetime", "ymdhms_compact"]:
            return dt.strftime("%Y%m%d%H%M%S")
        elif fmt_lower in ["rfc2822"]:
            return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")

        # Se já tiver diretivas com %, usa strftime direto
        if "%" in fmt_clean:
            return dt.strftime(fmt_clean)

        # Formatação human-friendly: YYYY, YY, MMMM, MMM, MM, DD, HH, hh, mm, ss, SSS, A, a
        ms = f"{int(dt.microsecond / 1000):03d}"
        out = fmt_clean
        out = out.replace("YYYY", dt.strftime("%Y"))
        out = out.replace("YY", dt.strftime("%y"))
        out = out.replace("MMMM", dt.strftime("%B"))
        out = out.replace("MMM", dt.strftime("%b"))
        out = out.replace("MM", dt.strftime("%m"))
        out = out.replace("DD", dt.strftime("%d"))
        out = out.replace("HH", dt.strftime("%H"))
        out = out.replace("hh", dt.strftime("%I"))
        out = out.replace("mm", dt.strftime("%M"))
        out = out.replace("ss", dt.strftime("%S"))
        out = out.replace("SSS", ms)
        out = out.replace("A", dt.strftime("%p"))
        out = out.replace("a", dt.strftime("%p").lower())
        return out

    @classmethod
    def interpolate_template(cls, template_str: Optional[str], request_data: Dict[str, Any]) -> str:
        """
        Interpola tags dinâmicas Faker e parâmetros da requisição no template da resposta.
        Suporta:
        - Body: {{req.body}}, {{req.body.id}}, {{req.body.user.name}}, {{req.body.items.0.name}}, "...req.body", {{req.body_merge(...)}}
        - Header: {{req.headers.Authorization}}, {{req.header.x-request-id}} (case-insensitive)
        - Param/Query: {{req.query.id}}, {{req.param.id}}, {{req.params.id}}
        - Datas em múltiplos formatos: {{date.iso}}, {{date.br}}, {{date.br_datetime}}, {{date.epoch}}, {{date:YYYY-MM-DD}}, {{date:+30d:iso}}
        - URL/Rota/Método: {{req.path}}, {{req.url}}, {{req.method}}
        - Faker & Timestamps: {{timestamp}}, {{datetime}}, {{faker.uuid}}, {{faker.name}}, etc.
        """
        if not template_str:
            return ""

        result = str(template_str)

        # 1. Tags de Data / Hora / UUID / Múltiplos Formatos
        now = datetime.utcnow()
        result = re.sub(r"\{\{\s*timestamp\s*\}\}", now.isoformat() + "Z", result)
        result = re.sub(r"\{\{\s*datetime\s*\}\}", now.strftime("%Y-%m-%d %H:%M:%S"), result)

        def resolve_date_tag(match):
            spec = match.group(1)
            if not spec:
                return now.strftime("%Y-%m-%dT%H:%M:%SZ")
            spec = spec.strip()
            offset = ""
            fmt = "iso"

            if spec.startswith("."):
                fmt = spec[1:].strip()
            elif spec.startswith(":"):
                raw_parts = [p.strip() for p in spec[1:].split(":") if p.strip()]
                if len(raw_parts) == 1:
                    if re.match(r"^[+-]\d+\s*[dhmy]$", raw_parts[0].lower()):
                        offset = raw_parts[0]
                        fmt = "iso"
                    else:
                        fmt = raw_parts[0]
                elif len(raw_parts) >= 2:
                    if re.match(r"^[+-]\d+\s*[dhmy]$", raw_parts[0].lower()):
                        offset = raw_parts[0]
                        fmt = ":".join(raw_parts[1:])
                    else:
                        fmt = ":".join(raw_parts)

            target_dt = cls.apply_date_offset(now, offset)
            return cls.format_datetime_custom(target_dt, fmt)

        result = re.sub(r"\{\{\s*(?:date|now)((?:[.:][^}]+)?)\s*\}\}", resolve_date_tag, result)

        # 2. Faker dinâmicos
        first_names = ["Ana", "Bruno", "Carlos", "Daniela", "Eduardo", "Fernanda", "Gabriel", "Helena", "Igor", "Juliana"]
        last_names = ["Silva", "Santos", "Oliveira", "Souza", "Rodrigues", "Ferreira", "Alves", "Pereira", "Lima", "Gomes"]
        domains = ["example.com", "testmail.org", "flowqa.io", "mockdev.com"]

        if re.search(r"\{\{\s*faker\.name\s*\}\}", result):
            name = f"{random.choice(first_names)} {random.choice(last_names)}"
            result = re.sub(r"\{\{\s*faker\.name\s*\}\}", name, result)

        if re.search(r"\{\{\s*faker\.email\s*\}\}", result):
            email = f"user_{random.randint(100, 999)}@{random.choice(domains)}"
            result = re.sub(r"\{\{\s*faker\.email\s*\}\}", email, result)

        if re.search(r"\{\{\s*faker\.uuid\s*\}\}", result):
            result = re.sub(r"\{\{\s*faker\.uuid\s*\}\}", str(uuid.uuid4()), result)

        if re.search(r"\{\{\s*faker\.(?:int|random_int)\s*\}\}", result):
            val_str = str(random.randint(1000, 99999))
            result = re.sub(r"\{\{\s*faker\.(?:int|random_int)\s*\}\}", val_str, result)

        if re.search(r"\{\{\s*faker\.company\s*\}\}", result):
            companies = ["TechCorp Solutions", "Global Logistics", "FinTech Pay", "Acme Enterprise"]
            result = re.sub(r"\{\{\s*faker\.company\s*\}\}", random.choice(companies), result)

        # 3. Preparação dos dados da requisição
        path_val = str(request_data.get("path", ""))
        url_val = str(request_data.get("url", ""))
        method_val = str(request_data.get("method", ""))
        query_params = request_data.get("query_params", {}) or {}
        headers = request_data.get("headers", {}) or {}
        headers_lower = {k.lower(): str(v) for k, v in headers.items()}
        json_body = request_data.get("json_body", {})
        body_text = request_data.get("body_text", "")

        # Se json_body estiver vazio mas houver body_text tipo urlencoded, parse
        if not json_body and body_text and isinstance(body_text, str) and "=" in body_text and not body_text.strip().startswith(("{", "[")):
            try:
                from urllib.parse import parse_qs
                parsed_form = parse_qs(body_text)
                json_body = {k: v[0] if len(v) == 1 else v for k, v in parsed_form.items()}
            except Exception:
                pass

        def format_val(val: Any) -> str:
            if val is None:
                return ""
            if isinstance(val, bool):
                return "true" if val else "false"
            if isinstance(val, (dict, list)):
                return json.dumps(val, ensure_ascii=False)
            return str(val)

        # 4. Caso especial: "{{req.body}}" ou {{req.body}}
        if isinstance(json_body, (dict, list)) and json_body:
            json_body_formatted = json.dumps(json_body, ensure_ascii=False, indent=2)
            result = re.sub(r'"\{\{\s*req\.body\s*\}\}"', json_body_formatted, result)
            result = re.sub(r'\{\{\s*req\.body\s*\}\}', json_body_formatted, result)
        elif body_text:
            result = re.sub(r'\{\{\s*req\.body\s*\}\}', str(body_text), result)
        else:
            result = re.sub(r'\{\{\s*req\.body\s*\}\}', "{}", result)

        # 5. Suporte a Spread de Body: "...req.body" ou "{{...req.body}}"
        if isinstance(json_body, dict) and json_body:
            inner_json_items = []
            for k, v in json_body.items():
                inner_json_items.append(f'  "{k}": {json.dumps(v, ensure_ascii=False)}')
            inner_body_str = ",\n".join(inner_json_items)

            result = re.sub(r'["\'](?:\.\.\.req\.body|\{\{\s*\.\.\.req\.body\s*\}\})["\']\s*,?', inner_body_str + ",\n", result)
            result = re.sub(r',\s*["\'](?:\.\.\.req\.body|\{\{\s*\.\.\.req\.body\s*\}\})["\']', ",\n" + inner_body_str, result)
            result = re.sub(r'["\'](?:\.\.\.req\.body|\{\{\s*\.\.\.req\.body\s*\}\})["\']\s*:\s*[^,\n\}]+,?', inner_body_str + ",\n", result)
            result = re.sub(r'\.\.\.req\.body\s*,?', inner_body_str + ",\n", result)

            result = re.sub(r',\s*,', ',', result)
            result = re.sub(r',\s*\}', '\n}', result)
        else:
            result = re.sub(r'["\']?(?:\.\.\.req\.body|\{\{\s*\.\.\.req\.body\s*\}\})["\']?\s*,?', '', result)
            result = re.sub(r',\s*\}', '\n}', result)

        # 6. Suporte a req.body_merge(...)
        def resolve_body_merge(match):
            extra_content = match.group(1).strip()
            extra_interpolated = cls.interpolate_template(extra_content, request_data)
            try:
                extra_dict = json.loads(extra_interpolated)
            except Exception:
                extra_dict = {}

            merged = {}
            if isinstance(json_body, dict):
                merged.update(json_body)
            if isinstance(extra_dict, dict):
                merged.update(extra_dict)

            return json.dumps(merged, ensure_ascii=False, indent=2)

        result = re.sub(r"\{\{\s*req\.body_merge\s*\((.*?)\)\s*\}\}", resolve_body_merge, result, flags=re.DOTALL)

        # 7. Regex unificado para resolução dinâmica de {{req.TARGET.FIELD}} ou {{req.TARGET}}
        def resolve_req_tag(match):
            target = match.group(1).lower()
            field = match.group(2)

            if target == "path":
                return path_val
            elif target == "url":
                return url_val
            elif target == "method":
                return method_val
            elif target in ["query", "param", "params"]:
                if not field:
                    return json.dumps(query_params, ensure_ascii=False)
                if field in query_params:
                    return str(query_params[field])
                field_lower = field.lower()
                for qk, qv in query_params.items():
                    if qk.lower() == field_lower:
                        return str(qv)
                return ""
            elif target in ["header", "headers"]:
                if not field:
                    return json.dumps(headers, ensure_ascii=False)
                if field in headers:
                    return str(headers[field])
                return headers_lower.get(field.lower(), "")
            elif target == "body":
                if not field:
                    return ""
                val = cls.get_nested_val(json_body, field)
                if val is not None:
                    return format_val(val)
                return ""

            return match.group(0)

        # Se houver tags entre aspas duplas de campos que retornam objetos/arrays, remove as aspas envoltórias
        if isinstance(json_body, dict):
            for k, v in json_body.items():
                if isinstance(v, (dict, list)):
                    formatted_v = json.dumps(v, ensure_ascii=False)
                    result = re.sub(rf'"\{{\{{\s*req\.body\.{re.escape(k)}\s*\}}\}}"', formatted_v, result)

        result = re.sub(
            r"\{\{\s*req\.(body|query|param|params|header|headers|path|url|method)(?:\.([^}]+?))?\s*\}\}",
            resolve_req_tag,
            result
        )

        return result

    @staticmethod
    def check_condition_operator(actual_val: Any, operator: str, expected_val: str) -> bool:
        if actual_val is None:
            return False
        str_actual = str(actual_val).lower().strip()
        str_expected = str(expected_val).lower().strip()
        op = operator.lower().strip()

        if op in ["equals", "igual", "=="]:
            return str_actual == str_expected or str_actual.lstrip("/") == str_expected.lstrip("/")
        elif op in ["starts_with", "inicia_com", "startswith"]:
            return str_actual.startswith(str_expected) or str_actual.lstrip("/").startswith(str_expected.lstrip("/"))
        elif op in ["contains", "contem", "includes"]:
            return str_expected in str_actual
        return str_actual == str_expected

    @staticmethod
    def get_nested_val(data: Any, path: str) -> Any:
        if not path or data is None:
            return data
        if not isinstance(data, (dict, list)):
            return str(data)
        parts = path.split('.')
        curr = data
        for p in parts:
            if isinstance(curr, dict) and p in curr:
                curr = curr[p]
            elif isinstance(curr, list) and p.isdigit() and int(p) < len(curr):
                curr = curr[int(p)]
            else:
                return None
        return curr

    @classmethod
    def match_rule(
        cls,
        rules: list[MockRuleDB],
        method: str,
        path: str,
        headers: Dict[str, str],
        query_params: Dict[str, str],
        body_text: str,
        raw_url: Optional[str] = None
    ) -> Optional[MockRuleDB]:
        """
        Encontra a regra ideal que atende aos critérios de matching da requisição.
        ESTRATÉGIA:
        1. Ordena regras por prioridade CRESCENTE (1 é executado PRIMEIRO, depois 2, 3, 4, 5).
        2. Avalia regras COM condições que bateram 100% com a requisição.
        3. Se nenhuma condição bateu, busca a regra sem condições (padrão 200 OK) para o mesmo endpoint/método.
        """
        clean_method = method.upper()
        clean_path = path if path.startswith("/") else f"/{path}"

        headers_lower = {k.lower(): str(v) for k, v in headers.items()}
        json_body = {}
        if body_text:
            try:
                json_body = json.loads(body_text)
            except Exception:
                pass

        matching_path_rules = []

        # Filtrar regras que batem com método e rota
        for rule in rules:
            if not rule.is_active:
                continue

            if rule.method != "ANY" and rule.method.upper() != clean_method:
                continue

            rule_path = rule.path_pattern or "/"
            if not rule_path.startswith("/") and rule_path != "*":
                rule_path = f"/{rule_path}"

            pattern = rule_path.replace("*", ".*")
            pattern = re.sub(r'\{[a-zA-Z0-9_]+\}', '[^/]+', pattern)

            # Verificar se a regra possui condição que avalia URL/Path
            has_url_condition = False
            raw_conditions = rule.match_headers if isinstance(rule.match_headers, list) else (
                rule.match_query_params if isinstance(rule.match_query_params, list) else None
            )
            if raw_conditions:
                for c in raw_conditions:
                    if isinstance(c, dict) and c.get("target", "").lower() in ["url", "path", "rota"]:
                        has_url_condition = True
                        break

            # Se bate com a rota, ou se o path_pattern é curinga (*), ou se tem condição de URL e path_pattern genérico
            if (
                re.fullmatch(f"^{pattern}$", clean_path)
                or clean_path == rule.path_pattern
                or clean_path == rule_path
                or rule.path_pattern == "*"
                or (has_url_condition and rule_path in ["/", "/*", "*"])
            ):
                matching_path_rules.append(rule)

        # ORDENAR POR PRIORIDADE CRESCENTE (1 = Primeiro a ser avaliado)
        matching_path_rules.sort(key=lambda r: (r.priority if (r.priority is not None and r.priority > 0) else 99))

        # PASS 1: Testar Regras COM Condições Específicas (Ordem de Prioridade 1 -> 6+)
        for rule in matching_path_rules:
            rule_conditions = None
            if isinstance(rule.match_headers, list):
                rule_conditions = rule.match_headers
            elif isinstance(rule.match_query_params, list):
                rule_conditions = rule.match_query_params

            if rule_conditions and isinstance(rule_conditions, list) and len(rule_conditions) > 0:
                cond_failed = False
                for cond in rule_conditions:
                    target = cond.get("target", "header").lower()
                    field = cond.get("field", "").strip()
                    operator = cond.get("operator", "equals")
                    expected = str(cond.get("value", ""))

                    if target == "header":
                        actual = headers_lower.get(field.lower())
                    elif target == "query":
                        actual = query_params.get(field)
                    elif target == "body":
                        if field:
                            actual = cls.get_nested_val(json_body, field)
                        else:
                            actual = body_text
                    elif target in ["url", "path", "rota"]:
                        query_str = "&".join([f"{k}={v}" for k, v in query_params.items()])
                        full_path = f"{clean_path}?{query_str}" if query_str else clean_path
                        full_request_url = raw_url if raw_url else full_path

                        # Captura a URL inteira (sem chave)
                        if operator in ["contains", "contem", "includes"]:
                            exp_lower = expected.lower().strip()
                            if (
                                exp_lower in clean_path.lower()
                                or exp_lower in full_path.lower()
                                or (full_request_url and exp_lower in full_request_url.lower())
                            ):
                                actual = exp_lower
                            else:
                                actual = full_path
                        elif operator in ["starts_with", "inicia_com", "startswith"]:
                            actual = clean_path
                        else:
                            actual = full_path
                    else:
                        actual = None

                    if not cls.check_condition_operator(actual, operator, expected):
                        cond_failed = True
                        break

                if not cond_failed:
                    return rule

            elif rule.match_body_pattern:
                if body_text and re.search(rule.match_body_pattern, body_text):
                    return rule

        # PASS 2: Se nenhuma condição bateu, busca a Regra SEM Condições (Default 200 OK da rota)
        for rule in matching_path_rules:
            rule_conditions = None
            if isinstance(rule.match_headers, list):
                rule_conditions = rule.match_headers
            elif isinstance(rule.match_query_params, list):
                rule_conditions = rule.match_query_params

            has_conditions = (rule_conditions and len(rule_conditions) > 0) or bool(rule.match_body_pattern)

            # Se não tem condições específicas, é a resposta padrão da rota!
            if not has_conditions:
                return rule

        # PASS 3: Se houver qualquer regra de sucesso (status < 400) para a rota, usa ela
        for rule in matching_path_rules:
            if rule.response_status < 400:
                return rule

        return None

    @classmethod
    async def process_mock_request(
        cls,
        db: Session,
        mock: ServiceMockDB,
        method: str,
        path: str,
        headers: Dict[str, str],
        query_params: Dict[str, str],
        body_text: str,
        client_ip: Optional[str] = None,
        raw_url: Optional[str] = None
    ) -> Tuple[int, Dict[str, str], str]:
        """
        Executa a interceptação de uma requisição mockada.
        - Validação Automática: Se a requisição for malformada ou fora do padrão (ex: JSON corrompido), RETORNA 400 BAD REQUEST.
        - Caso contrário, atende à regra de matching ou RETORNA 200 OK DEFAULT (Sucesso).
        """
        headers_lower = {k.lower(): str(v) for k, v in headers.items()}
        content_type = headers_lower.get("content-type", "").lower()

        is_json_request = "json" in content_type or (method in ["POST", "PUT", "PATCH"] and body_text and body_text.strip().startswith(("{", "[")))
        is_malformed_json = False
        json_error_detail = ""

        json_body = {}
        if body_text and body_text.strip():
            try:
                json_body = json.loads(body_text)
            except Exception as json_err:
                if is_json_request:
                    is_malformed_json = True
                    json_error_detail = str(json_err)

        # 0. VALIDAÇÃO AUTOMÁTICA DE BAD REQUEST (HTTP 400)
        if is_malformed_json:
            status_code = 400
            resp_headers = {"Content-Type": "application/json"}
            raw_body = json.dumps({
                "error": "Bad Request",
                "message": f"A requisição enviada para {method} {path} está fora do padrão esperado ou contém um JSON malformado.",
                "details": json_error_detail,
                "mock_slug": mock.slug
            }, indent=2)

            final_body = cls.interpolate_template(raw_body, {"query_params": query_params, "headers": headers, "json_body": {}, "path": path, "url": path, "method": method})

            try:
                log_entry = MockLogDB(
                    mock_id=mock.id,
                    rule_id=None,
                    client_ip=client_ip,
                    method=method,
                    path=path,
                    request_headers=headers,
                    request_body=body_text[:2000] if body_text else None,
                    response_status=400,
                    response_body=final_body[:2000] if final_body else None,
                    executed_at=datetime.utcnow()
                )
                db.add(log_entry)
                db.commit()
            except Exception:
                db.rollback()

            return 400, resp_headers, final_body

        query_str = "&".join([f"{k}={v}" for k, v in query_params.items()])
        full_url = f"{path}?{query_str}" if query_str else path

        request_data = {
            "query_params": query_params,
            "headers": headers,
            "json_body": json_body,
            "body_text": body_text,
            "path": path,
            "url": raw_url if raw_url else full_url,
            "method": method
        }

        # 1. Encontrar Regra de Match
        matched_rule = cls.match_rule(mock.rules, method, path, headers, query_params, body_text, raw_url=raw_url)

        if matched_rule:
            status_code = matched_rule.response_status
            resp_headers = matched_rule.response_headers or {"Content-Type": "application/json"}
            raw_body = matched_rule.response_body or ""
            delay_ms = matched_rule.delay_ms or 0
            rule_id = matched_rule.id
        else:
            # ESTRATÉGIA DEFAULT: SE NENHUMA REGRA ESPECÍFICA FOR ATENDIDA -> RETORNA 200 OK SUCESSO!
            status_code = 200
            resp_headers = {"Content-Type": "application/json"}
            raw_body = json.dumps({
                "status": "success",
                "message": f"Resposta mock padrão de sucesso para {method} {path}",
                "mock_server": mock.name,
                "mock_slug": mock.slug,
                "timestamp": "{{timestamp}}"
            }, indent=2)
            delay_ms = 0
            rule_id = None

        # 2. Simular Latência de Rede
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000.0)

        # 3. Interpolador de Templating
        final_body = cls.interpolate_template(raw_body, request_data)

        # Interpolar cabeçalhos de resposta caso o usuário utilize tags dinâmicas
        final_headers = {}
        if isinstance(resp_headers, dict):
            for hk, hv in resp_headers.items():
                interp_k = cls.interpolate_template(hk, request_data)
                interp_v = cls.interpolate_template(str(hv), request_data)
                final_headers[interp_k] = interp_v
        else:
            final_headers = resp_headers

        # 4. Registra no Log de Inspeção
        try:
            log_entry = MockLogDB(
                mock_id=mock.id,
                rule_id=rule_id,
                client_ip=client_ip,
                method=method,
                path=path,
                request_headers=headers,
                request_body=body_text[:2000] if body_text else None,
                response_status=status_code,
                response_body=final_body[:2000] if final_body else None,
                executed_at=datetime.utcnow()
            )
            db.add(log_entry)
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"Erro ao salvar log de mock: {e}")

        return status_code, final_headers, final_body
