import asyncio
import json
import re
import uuid
import random
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session

from app.models.mock_models import ServiceMockDB, MockRuleDB, MockLogDB


class MockExecutionEngine:

    @staticmethod
    def interpolate_template(template_str: Optional[str], request_data: Dict[str, Any]) -> str:
        """
        Interpola tags dinâmicas Faker e parâmetros da requisição no template da resposta.
        Ex: {{faker.name}}, {{faker.email}}, {{req.query.id}}, {{req.body.username}}, {{timestamp}}
        """
        if not template_str:
            return ""

        result = str(template_str)

        # 1. Tags de Data / Hora / UUID
        now = datetime.utcnow()
        result = result.replace("{{timestamp}}", now.isoformat() + "Z")
        result = result.replace("{{datetime}}", now.strftime("%Y-%m-%d %H:%M:%S"))

        # 2. Faker dinamicos sem dependência externa rígida
        first_names = ["Ana", "Bruno", "Carlos", "Daniela", "Eduardo", "Fernanda", "Gabriel", "Helena", "Igor", "Juliana"]
        last_names = ["Silva", "Santos", "Oliveira", "Souza", "Rodrigues", "Ferreira", "Alves", "Pereira", "Lima", "Gomes"]
        domains = ["example.com", "testmail.org", "flowqa.io", "mockdev.com"]

        if "{{faker.name}}" in result:
            name = f"{random.choice(first_names)} {random.choice(last_names)}"
            result = result.replace("{{faker.name}}", name)

        if "{{faker.email}}" in result:
            email = f"user_{random.randint(100, 999)}@{random.choice(domains)}"
            result = result.replace("{{faker.email}}", email)

        if "{{faker.uuid}}" in result:
            result = result.replace("{{faker.uuid}}", str(uuid.uuid4()))

        if "{{faker.int}}" in result or "{{faker.random_int}}" in result:
            val_str = str(random.randint(1000, 99999))
            result = result.replace("{{faker.int}}", val_str).replace("{{faker.random_int}}", val_str)

        if "{{faker.company}}" in result:
            companies = ["TechCorp Solutions", "Global Logistics", "FinTech Pay", "Acme Enterprise"]
            result = result.replace("{{faker.company}}", random.choice(companies))

        # 3. Interpolação de Parâmetros da Requisição
        # Query Params: {{req.query.key}}
        query_params = request_data.get("query_params", {})
        for k, v in query_params.items():
            result = result.replace(f"{{{{req.query.{k}}}}}", str(v))

        # Request Headers: {{req.headers.key}}
        headers = request_data.get("headers", {})
        for k, v in headers.items():
            result = result.replace(f"{{{{req.headers.{k}}}}}", str(v))

        # Request Body: {{req.body.key}}
        body_data = request_data.get("json_body", {})
        if isinstance(body_data, dict):
            for k, v in body_data.items():
                if isinstance(v, (str, int, float, bool)):
                    result = result.replace(f"{{{{req.body.{k}}}}}", str(v))

        return result

    @classmethod
    def match_rule(
        cls,
        rules: list[MockRuleDB],
        method: str,
        path: str,
        headers: Dict[str, str],
        query_params: Dict[str, str],
        body_text: str
    ) -> Optional[MockRuleDB]:
        """
        Encontra a regra de maior prioridade que atende aos critérios de matching da requisição.
        """
        clean_method = method.upper()
        clean_path = path if path.startswith("/") else f"/{path}"

        for rule in rules:
            if not rule.is_active:
                continue

            # 1. Matching de Método
            if rule.method != "ANY" and rule.method.upper() != clean_method:
                continue

            # 2. Matching de Path (Pattern Regex ou Exact ou Wildcard *)
            pattern = rule.path_pattern.replace("*", ".*")
            # Converter parâmetros como {id} para regex
            pattern = re.sub(r'\{[a-zA-Z0-9_]+\}', '[^/]+', pattern)

            if not re.fullmatch(f"^{pattern}$", clean_path):
                # Tentar comparação case-insensitive
                if clean_path != rule.path_pattern:
                    continue

            # 3. Matching de Headers (Se especificado, todos devem combinar)
            if rule.match_headers and isinstance(rule.match_headers, dict):
                headers_lower = {k.lower(): str(v) for k, v in headers.items()}
                match_failed = False
                for hk, hv in rule.match_headers.items():
                    if hk.lower() not in headers_lower or headers_lower[hk.lower()] != str(hv):
                        match_failed = True
                        break
                if match_failed:
                    continue

            # 4. Matching de Query Params (Se especificado, todos devem combinar)
            if rule.match_query_params and isinstance(rule.match_query_params, dict):
                match_failed = False
                for qk, qv in rule.match_query_params.items():
                    if qk not in query_params or str(query_params[qk]) != str(qv):
                        match_failed = True
                        break
                if match_failed:
                    continue

            # 5. Matching de Body (Se especificado)
            if rule.match_body_pattern:
                if not body_text or not re.search(rule.match_body_pattern, body_text):
                    continue

            # Se passou por todos os filtros, encontramos a regra correspondente!
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
        client_ip: Optional[str] = None
    ) -> Tuple[int, Dict[str, str], str]:
        """
        Executa a interceptação de uma requisição mockada.
        """
        # Parse JSON Body para interpolação
        json_body = {}
        if body_text:
            try:
                json_body = json.loads(body_text)
            except Exception:
                pass

        request_data = {
            "query_params": query_params,
            "headers": headers,
            "json_body": json_body
        }

        # 1. Encontrar Regra de Match
        matched_rule = cls.match_rule(mock.rules, method, path, headers, query_params, body_text)

        if matched_rule:
            status_code = matched_rule.response_status
            resp_headers = matched_rule.response_headers or {"Content-Type": "application/json"}
            raw_body = matched_rule.response_body or ""
            delay_ms = matched_rule.delay_ms or 0
            rule_id = matched_rule.id
        else:
            # Resposta Default Fallback
            status_code = 404
            resp_headers = {"Content-Type": "application/json"}
            raw_body = json.dumps({
                "error": "Mock Not Found",
                "message": f"Nenhuma regra atendeu à requisição {method} {path}",
                "mock_slug": mock.slug
            })
            delay_ms = 0
            rule_id = None

        # 2. Simular Latência de Rede
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000.0)

        # 3. Interpolador de Templating
        final_body = cls.interpolate_template(raw_body, request_data)

        # 4. Registra no Log de Inspecção
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

        return status_code, resp_headers, final_body
