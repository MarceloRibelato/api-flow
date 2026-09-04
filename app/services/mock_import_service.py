import json
import re
import shlex
import uuid
from typing import List, Dict, Any


class MockImportService:

    @staticmethod
    def generate_best_practice_responses(name: str, method: str, path_pattern: str) -> List[Dict[str, Any]]:
        """
        Gera 4 variações automáticas de resposta para o endpoint seguindo boas práticas:
        - 200 OK / 201 Created (payload dinâmico Faker)
        - 400 Bad Request
        - 401 Unauthorized
        - 500 Internal Server Error
        """
        clean_method = method.upper()
        clean_path = path_pattern if path_pattern.startswith("/") else f"/{path_pattern}"

        # Tentar adivinhar recurso base a partir da URL (ex: /api/v1/users/123 -> "user")
        resource_match = re.search(r'/([a-zA-Z0-9_-]+)/?$', clean_path)
        resource_name = resource_match.group(1).rstrip('s') if resource_match else "item"

        success_status = 201 if clean_method == "POST" else 200

        # Payload 200/201 dinâmico com tags Faker
        if clean_method == "GET" and clean_path.endswith("s"):
            # Lista de itens
            success_body = json.dumps([
                {
                    "id": 1,
                    "name": "{{faker.name}}",
                    "email": "{{faker.email}}",
                    "status": "active",
                    "createdAt": "{{timestamp}}"
                },
                {
                    "id": 2,
                    "name": "{{faker.name}}",
                    "email": "{{faker.email}}",
                    "status": "active",
                    "createdAt": "{{timestamp}}"
                }
            ], indent=2)
        else:
            # Objeto único
            success_body = json.dumps({
                "id": "{{faker.uuid}}",
                f"{resource_name}_id": "101",
                "name": "{{faker.name}}",
                "email": "{{faker.email}}",
                "status": "success",
                "message": f"Operação {clean_method} concluída para {resource_name}",
                "updatedAt": "{{timestamp}}"
            }, indent=2)

        # Regra 1: 200 OK / 201 Created
        rule_200 = {
            "name": f"[200 OK] {name} - Sucesso",
            "method": clean_method,
            "path_pattern": clean_path,
            "priority": 10,
            "response_status": success_status,
            "response_headers": {"Content-Type": "application/json"},
            "response_body": success_body,
            "delay_ms": 100,
            "is_active": True
        }

        # Regra 2: 400 Bad Request
        rule_400 = {
            "name": f"[400 Bad Request] {name} - Erro Validação",
            "method": clean_method,
            "path_pattern": clean_path,
            "priority": 5,
            "match_headers": {"X-Mock-Status": "400"},
            "response_status": 400,
            "response_headers": {"Content-Type": "application/json"},
            "response_body": json.dumps({
                "error": "Bad Request",
                "message": "Parâmetros inválidos na requisição.",
                "details": [
                    {"field": "id", "issue": "Campo obrigatório ausente ou inválido"}
                ],
                "code": 400
            }, indent=2),
            "delay_ms": 50,
            "is_active": True
        }

        # Regra 3: 401 Unauthorized
        rule_401 = {
            "name": f"[401 Unauthorized] {name} - Não Autorizado",
            "method": clean_method,
            "path_pattern": clean_path,
            "priority": 5,
            "match_headers": {"X-Mock-Status": "401"},
            "response_status": 401,
            "response_headers": {"Content-Type": "application/json"},
            "response_body": json.dumps({
                "error": "Unauthorized",
                "message": "Token de autenticação ausente ou expirado.",
                "code": 401
            }, indent=2),
            "delay_ms": 50,
            "is_active": True
        }

        # Regra 4: 500 Internal Server Error
        rule_500 = {
            "name": f"[500 Error] {name} - Falha Interna",
            "method": clean_method,
            "path_pattern": clean_path,
            "priority": 1,
            "match_headers": {"X-Mock-Status": "500"},
            "response_status": 500,
            "response_headers": {"Content-Type": "application/json"},
            "response_body": json.dumps({
                "error": "Internal Server Error",
                "message": "Ocorreu uma falha inesperada no servidor.",
                "code": 500
            }, indent=2),
            "delay_ms": 200,
            "is_active": True
        }

        return [rule_200, rule_400, rule_401, rule_500]

    @classmethod
    def parse_postman_collection(cls, collection_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extrai endpoints de uma coleção Postman v2.1"""
        rules = []

        def extract_items(items):
            for item in items:
                if "item" in item:
                    extract_items(item["item"])
                elif "request" in item:
                    req = item["request"]
                    name = item.get("name", "Postman Endpoint")
                    method = req.get("method", "GET")

                    url_obj = req.get("url", {})
                    if isinstance(url_obj, str):
                        raw_url = url_obj
                    elif isinstance(url_obj, dict):
                        raw_url = url_obj.get("raw", "")
                        if not raw_url and "path" in url_obj:
                            raw_url = "/" + "/".join(url_obj.get("path", []))

                    # Normalizar path
                    path_pattern = re.sub(r'https?://[^/]+', '', raw_url)
                    if not path_pattern.startswith("/"):
                        path_pattern = "/" + path_pattern

                    generated_rules = cls.generate_best_practice_responses(name, method, path_pattern)
                    rules.extend(generated_rules)

        items = collection_data.get("item", [])
        extract_items(items)
        return rules

    @classmethod
    def parse_swagger_spec(cls, spec_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extrai endpoints de documentação Swagger 2.0 / OpenAPI 3.0"""
        rules = []
        paths = spec_data.get("paths", {})

        for path_key, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method_key, details in methods.items():
                if method_key.lower() not in ["get", "post", "put", "delete", "patch"]:
                    continue
                summary = details.get("summary") or details.get("operationId") or f"{method_key.upper()} {path_key}"
                generated_rules = cls.generate_best_practice_responses(summary, method_key.upper(), path_key)
                rules.extend(generated_rules)

        return rules

    @classmethod
    def parse_curl_command(cls, curl_str: str) -> List[Dict[str, Any]]:
        """Extrai método, URL, headers e body de um comando cURL"""
        method = "GET"
        url = "/"
        headers = {}
        body = None

        try:
            tokens = shlex.split(curl_str.strip())
            i = 0
            while i < len(tokens):
                token = tokens[i]
                if token in ["-X", "--request"] and i + 1 < len(tokens):
                    method = tokens[i + 1].upper()
                    i += 2
                elif token in ["-H", "--header"] and i + 1 < len(tokens):
                    header_str = tokens[i + 1]
                    if ":" in header_str:
                        k, v = header_str.split(":", 1)
                        headers[k.strip()] = v.strip()
                    i += 2
                elif token in ["-d", "--data", "--data-raw", "--data-binary"] and i + 1 < len(tokens):
                    body = tokens[i + 1]
                    if method == "GET":
                        method = "POST"
                    i += 2
                elif token.startswith("http://") or token.startswith("https://") or token.startswith("/"):
                    url = token
                    i += 1
                else:
                    i += 1
        except Exception:
            pass

        path_pattern = re.sub(r'https?://[^/]+', '', url)
        if not path_pattern.startswith("/"):
            path_pattern = "/" + path_pattern

        return cls.generate_best_practice_responses("Imported cURL", method, path_pattern)
