from __future__ import annotations
import json
import re
import shlex
import uuid
from typing import List, Dict, Any, Optional


class MockImportService:

    @staticmethod
    def generate_best_practice_responses(name: str, method: str, path_pattern: str, custom_success_body: Optional[str] = None) -> List[Dict[str, Any]]:
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

        # Regra 1: 200 OK / 201 Created (Prioridade 1 = Resposta Padrão de Sucesso)
        status_label = f"{success_status} OK" if success_status == 200 else f"{success_status} Created"
        rule_200 = {
            "name": f"[{status_label}] {name} - Sucesso",
            "method": clean_method,
            "path_pattern": clean_path,
            "priority": 1,
            "response_status": success_status,
            "response_headers": {"Content-Type": "application/json"},
            "response_body": custom_success_body if (custom_success_body is not None and str(custom_success_body).strip() != "") else success_body,
            "delay_ms": 100,
            "is_active": True
        }

        # Regra 2: 400 Bad Request
        rule_400 = {
            "name": f"[400 Bad Request] {name} - Erro Validação",
            "method": clean_method,
            "path_pattern": clean_path,
            "priority": 2,
            "match_headers": [{"target": "query", "field": "error", "operator": "equals", "value": "400"}],
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
            "priority": 3,
            "match_headers": [{"target": "query", "field": "error", "operator": "equals", "value": "401"}],
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

        # Regra 4: 404 Not Found
        rule_404 = {
            "name": f"[404 Not Found] {name} - Não Encontrado",
            "method": clean_method,
            "path_pattern": clean_path,
            "priority": 4,
            "match_headers": [{"target": "query", "field": "error", "operator": "equals", "value": "404"}],
            "response_status": 404,
            "response_headers": {"Content-Type": "application/json"},
            "response_body": json.dumps({
                "error": "Not Found",
                "message": f"O recurso solicitado em {clean_path} não foi encontrado.",
                "code": 404
            }, indent=2),
            "delay_ms": 50,
            "is_active": True
        }

        # Regra 5: 500 Internal Server Error
        rule_500 = {
            "name": f"[500 Error] {name} - Falha Interna",
            "method": clean_method,
            "path_pattern": clean_path,
            "priority": 5,
            "match_headers": [{"target": "query", "field": "error", "operator": "equals", "value": "500"}],
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

        # Regra 6: 504 Gateway Timeout
        rule_504 = {
            "name": f"[504 Timeout] {name} - Tempo Limite Excedido",
            "method": clean_method,
            "path_pattern": clean_path,
            "priority": 6,
            "match_headers": [{"target": "query", "field": "error", "operator": "equals", "value": "504"}],
            "response_status": 504,
            "response_headers": {"Content-Type": "application/json"},
            "response_body": json.dumps({
                "error": "Gateway Timeout",
                "message": "O tempo limite de comunicação com o serviço upstream foi excedido.",
                "code": 504
            }, indent=2),
            "delay_ms": 5000,
            "is_active": True
        }

        return [rule_200, rule_400, rule_401, rule_404, rule_500, rule_504]

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
    def _format_body(cls, body: Any) -> Optional[str]:
        if body is None:
            return None
        if isinstance(body, str):
            try:
                return json.dumps(json.loads(body), indent=2)
            except Exception:
                return body
        try:
            return json.dumps(body, indent=2)
        except Exception:
            return str(body)

    @classmethod
    def resolve_ref(cls, ref_str: str, root: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(ref_str, str) or not ref_str.startswith("#/"):
            return None
        parts = ref_str.lstrip("#/").split("/")
        curr = root
        for p in parts:
            if isinstance(curr, dict) and p in curr:
                curr = curr[p]
            else:
                return None
        return curr

    @classmethod
    def generate_sample(cls, schema: Dict[str, Any], root: Dict[str, Any], depth: int = 0) -> Any:
        if depth > 5 or not isinstance(schema, dict):
            return {}
        if "$ref" in schema:
            resolved = cls.resolve_ref(schema["$ref"], root)
            if resolved:
                return cls.generate_sample(resolved, root, depth + 1)
            return {}
        if "example" in schema and schema["example"] is not None:
            return schema["example"]
        if "examples" in schema and isinstance(schema["examples"], list) and schema["examples"]:
            return schema["examples"][0]

        stype = schema.get("type")
        if stype == "array" or "items" in schema:
            item_schema = schema.get("items", {})
            return [cls.generate_sample(item_schema, root, depth + 1)]

        if "allOf" in schema and isinstance(schema["allOf"], list):
            merged = {}
            for sub in schema["allOf"]:
                sub_sample = cls.generate_sample(sub, root, depth + 1)
                if isinstance(sub_sample, dict):
                    merged.update(sub_sample)
            return merged

        if stype == "object" or "properties" in schema or not stype:
            obj = {}
            props = schema.get("properties", {})
            if not props and stype != "object" and "type" in schema:
                # É um tipo primitivo
                stype = schema.get("type")
            else:
                for k, prop in props.items():
                    if isinstance(prop, dict) and "$ref" in prop:
                        prop = cls.resolve_ref(prop["$ref"], root) or prop
                    ptype = prop.get("type", "string") if isinstance(prop, dict) else "string"
                    if isinstance(prop, dict) and "example" in prop and prop["example"] is not None:
                        obj[k] = prop["example"]
                    elif isinstance(prop, dict) and "enum" in prop and prop["enum"]:
                        val = prop["enum"][0]
                        obj[k] = "on" if val is True else ("off" if val is False else val)
                    elif ptype in ["integer", "number"]:
                        obj[k] = prop.get("minimum", 0) if isinstance(prop, dict) else 0
                    elif ptype == "boolean":
                        obj[k] = True
                    elif ptype == "array" or (isinstance(prop, dict) and "items" in prop):
                        item_schema = prop.get("items", {}) if isinstance(prop, dict) else {}
                        obj[k] = [cls.generate_sample(item_schema, root, depth + 1)]
                    elif ptype == "object" or (isinstance(prop, dict) and "properties" in prop):
                        obj[k] = cls.generate_sample(prop, root, depth + 1)
                    else:
                        fmt = prop.get("format", "") if isinstance(prop, dict) else ""
                        if fmt == "date-time":
                            obj[k] = "{{timestamp}}"
                        elif fmt == "uuid":
                            obj[k] = "{{faker.uuid}}"
                        elif fmt == "email":
                            obj[k] = "{{faker.email}}"
                        else:
                            obj[k] = f"sample_{k}"
                return obj

        if stype in ["integer", "number"]:
            return schema.get("minimum", 0)
        if stype == "boolean":
            return True
        return "sample"

    @classmethod
    def parse_swagger_spec(cls, spec_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extrai endpoints de documentação Swagger 2.0 / OpenAPI 3.0"""
        rules = []
        paths = spec_data.get("paths", {})
        if not isinstance(paths, dict):
            return rules

        for path_key, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method_key, details in methods.items():
                if not isinstance(details, dict):
                    continue
                if method_key.lower() not in ["get", "post", "put", "delete", "patch"]:
                    continue
                summary = details.get("summary") or details.get("operationId") or f"{method_key.upper()} {path_key}"

                custom_success_body = None
                responses = details.get("responses", {})
                if isinstance(responses, dict):
                    success_codes = ["200", "201", 200, 201, "default"]
                    for k in responses.keys():
                        if str(k).startswith("2") and k not in success_codes:
                            success_codes.append(k)

                    for success_code in success_codes:
                        resp_item = responses.get(success_code)
                        if isinstance(resp_item, dict):
                            # Resolução de $ref na resposta
                            if "$ref" in resp_item:
                                resp_item = cls.resolve_ref(resp_item["$ref"], spec_data) or resp_item

                            # 1. Swagger 2.0 examples
                            examples = resp_item.get("examples", {})
                            if isinstance(examples, dict) and examples:
                                body = examples.get("application/json") or next(iter(examples.values()), None)
                                if body is not None:
                                    custom_success_body = cls._format_body(body)
                                    break

                            # 2. OpenAPI 3.0 content
                            content = resp_item.get("content", {})
                            if isinstance(content, dict) and content:
                                app_json = content.get("application/json") or next(iter(content.values()), {})
                                if isinstance(app_json, dict):
                                    if "example" in app_json and app_json["example"] is not None:
                                        custom_success_body = cls._format_body(app_json["example"])
                                        break
                                    if "examples" in app_json and isinstance(app_json["examples"], dict) and app_json["examples"]:
                                        first_ex = next(iter(app_json["examples"].values()), {})
                                        ex_val = first_ex.get("value") if isinstance(first_ex, dict) and "value" in first_ex else first_ex
                                        if ex_val is not None:
                                            custom_success_body = cls._format_body(ex_val)
                                            break
                                    if "schema" in app_json and isinstance(app_json["schema"], dict):
                                        sample_from_schema = cls.generate_sample(app_json["schema"], spec_data)
                                        if sample_from_schema:
                                            custom_success_body = json.dumps(sample_from_schema, indent=2)
                                            break

                            # 3. Direct example
                            if "example" in resp_item and resp_item["example"] is not None:
                                custom_success_body = cls._format_body(resp_item["example"])
                                break

                            # 4. Swagger 2.0 schema
                            resp_schema = resp_item.get("schema")
                            if isinstance(resp_schema, dict):
                                sample_from_schema = cls.generate_sample(resp_schema, spec_data)
                                if sample_from_schema:
                                    custom_success_body = json.dumps(sample_from_schema, indent=2)
                                    break

                full_path_key = path_key
                generated_rules = cls.generate_best_practice_responses(
                    summary,
                    method_key.upper(),
                    full_path_key,
                    custom_success_body=custom_success_body
                )
                rules.extend(generated_rules)

        return rules

    @classmethod
    def parse_asyncapi_spec(cls, spec_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extrai endpoints/canais de documentação AsyncAPI 2.x e 3.x (Kafka, RabbitMQ, WebSockets, MQTT)"""
        rules = []
        channels = spec_data.get("channels", {})
        if not isinstance(channels, dict):
            return rules

        for ch_name, ch_data in channels.items():
            if not isinstance(ch_data, dict):
                continue

            actual_address = ch_data.get("address") or ch_name
            path_pattern = f"/{actual_address.lstrip('/')}"

            operations = []
            if "publish" in ch_data and isinstance(ch_data["publish"], dict):
                operations.append(("POST", ch_data["publish"]))
            if "subscribe" in ch_data and isinstance(ch_data["subscribe"], dict):
                operations.append(("POST", ch_data["subscribe"]))

            if not operations:
                operations.append(("POST", ch_data))

            for method, op_details in operations:
                op_id = op_details.get("operationId") or op_details.get("summary") or ch_name
                summary = op_details.get("summary") or op_id

                msg = op_details.get("message", {})
                if isinstance(msg, dict) and "$ref" in msg:
                    msg = cls.resolve_ref(msg["$ref"], spec_data) or msg

                payload_schema = msg.get("payload", {}) if isinstance(msg, dict) else {}
                if isinstance(payload_schema, dict) and "$ref" in payload_schema:
                    payload_schema = cls.resolve_ref(payload_schema["$ref"], spec_data) or payload_schema

                sample_payload = None
                if payload_schema:
                    sample_dict = cls.generate_sample(payload_schema, spec_data)
                    if sample_dict:
                        sample_payload = json.dumps(sample_dict, indent=2)

                generated_rules = cls.generate_best_practice_responses(
                    summary,
                    method,
                    path_pattern,
                    custom_success_body=sample_payload
                )
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
