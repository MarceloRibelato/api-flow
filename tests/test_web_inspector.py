import sys
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

# Ensure mock modules exist if running in environment where optional database/ORM packages are inside container
mock_db_module = MagicMock()
mock_models_module = MagicMock()
mock_analysis_module = MagicMock()
mock_skill_module = MagicMock()

if "app.database" not in sys.modules:
    sys.modules["app.database"] = mock_db_module
if "app.models.agent_models" not in sys.modules:
    sys.modules["app.models.agent_models"] = mock_models_module
if "app.services.analysis_service" not in sys.modules:
    sys.modules["app.services.analysis_service"] = mock_analysis_module
if "app.services.skill_service" not in sys.modules:
    sys.modules["app.services.skill_service"] = mock_skill_module

from app.services.web_inspector_service import _validate_url, _WebInspectorServiceImpl

class TestWebInspectorSecurityAndSSRF(unittest.TestCase):
    """Test suite validating anti-SSRF protections in WebInspectorService."""

    def test_validate_url_allows_valid_public_urls(self):
        """Allows valid public HTTP/HTTPS URLs."""
        valid_urls = [
            "https://example.com",
            "http://example.com/login",
            "https://app.qa-flow.com:8443/dashboard",
            "http://8.8.8.8",
            "https://1.1.1.1/dns-query"
        ]
        for url in valid_urls:
            try:
                _validate_url(url)
            except ValueError as e:
                self.fail(f"Valid URL '{url}' was incorrectly rejected: {e}")

    def test_validate_url_blocks_internal_docker_hostnames(self):
        """Blocks internal docker hostnames."""
        blocked_hosts = [
            "http://flow-backend:8000/api",
            "http://flow-db:5432",
            "http://flow-redis:6379",
            "http://flow-frontend:3000",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://0.0.0.0:8000",
            "http://host.docker.internal:8000"
        ]
        for url in blocked_hosts:
            with self.assertRaises(ValueError, msg=f"Internal host '{url}' should be blocked"):
                _validate_url(url)

    def test_validate_url_blocks_private_ip_ranges(self):
        """Blocks RFC 1918 private IP addresses."""
        private_ips = [
            "http://10.0.0.1/admin",
            "http://172.16.0.5:8080",
            "http://172.31.255.255",
            "http://192.168.1.100/router",
            "http://127.0.0.2"
        ]
        for url in private_ips:
            with self.assertRaises(ValueError, msg=f"Private IP '{url}' should be blocked"):
                _validate_url(url)

    def test_validate_url_blocks_cloud_metadata(self):
        """Blocks AWS/GCP/Azure link-local cloud metadata IP (169.254.169.254)."""
        metadata_urls = [
            "http://169.254.169.254/latest/meta-data/",
            "https://169.254.169.254/computeMetadata/v1/"
        ]
        for url in metadata_urls:
            with self.assertRaises(ValueError, msg=f"Cloud metadata IP '{url}' should be blocked"):
                _validate_url(url)

    def test_validate_url_blocks_unsupported_protocols(self):
        """Blocks non-HTTP/HTTPS protocols."""
        bad_protocols = [
            "file:///etc/passwd",
            "ftp://ftp.example.com/file",
            "gopher://example.com",
            "javascript:alert(1)"
        ]
        for url in bad_protocols:
            with self.assertRaises(ValueError, msg=f"Protocol in '{url}' should be blocked"):
                _validate_url(url)


class TestWebInspectorAutoCorrection(unittest.IsolatedAsyncioTestCase):
    """Test suite validating icon-based candidate scoring and AI auto-correction."""

    async def test_ai_autocorrect_matches_icon_synonyms(self):
        """Validates that a step targeting 'carrinho' matches an icon-only button with 'shopping-cart'."""
        mock_tree = [
            {
                "id": "lws-0",
                "tagName": "BUTTON",
                "text": "[icon: shopping-cart]",
                "iconAttr": "shopping-cart",
                "placeholder": "",
                "nameAttr": "",
                "idAttr": "btn-checkout",
                "labelAttr": "",
                "selector": "#btn-checkout"
            },
            {
                "id": "lws-1",
                "tagName": "BUTTON",
                "text": "Outro Botão",
                "iconAttr": "",
                "placeholder": "",
                "nameAttr": "",
                "idAttr": "btn-other",
                "labelAttr": "",
                "selector": "#btn-other"
            }
        ]

        failed_step = {
            "name": "Clicar no carrinho",
            "type": "click",
            "properties": {"selector": "#old-cart-selector-broken"}
        }

        with patch.object(_WebInspectorServiceImpl, "get_snapshot", new_callable=AsyncMock) as mock_snap, \
             patch.object(_WebInspectorServiceImpl, "generate_selectors_for_element", new_callable=AsyncMock) as mock_gen:
            
            mock_snap.return_value = ({"tree": mock_tree}, None)
            mock_gen.return_value = {
                "success": True,
                "selectors": [{"name": "ID", "value": "#btn-checkout", "count": 1}]
            }

            # Inject a fake active session
            from app.services.web_inspector_service import _active_sessions
            _active_sessions["test-session-1"] = {"user_id": 1}

            try:
                res = await _WebInspectorServiceImpl.ai_analyze_full_tree_and_correct(
                    "test-session-1", failed_step, user_id=1
                )
                self.assertTrue(res.get("success"))
                self.assertEqual(res.get("suggested_selector"), "#btn-checkout")
                self.assertEqual(res.get("matched_node", {}).get("id"), "lws-0")
            finally:
                _active_sessions.pop("test-session-1", None)

    async def test_ai_autocorrect_matches_trash_icon_for_excluir(self):
        """Validates that a step 'Excluir item' matches an icon button with 'lucide-trash-2'."""
        mock_tree = [
            {
                "id": "lws-delete",
                "tagName": "BUTTON",
                "text": "[icon: trash-2]",
                "iconAttr": "trash-2",
                "placeholder": "",
                "nameAttr": "",
                "idAttr": "",
                "labelAttr": "",
                "selector": "button.delete-action"
            },
            {
                "id": "lws-save",
                "tagName": "BUTTON",
                "text": "Salvar",
                "iconAttr": "save",
                "placeholder": "",
                "nameAttr": "",
                "idAttr": "save-btn",
                "labelAttr": "",
                "selector": "#save-btn"
            }
        ]

        failed_step = {
            "name": "Excluir item da lista",
            "type": "click",
            "properties": {"selector": "#btn-remove-old"}
        }

        with patch.object(_WebInspectorServiceImpl, "get_snapshot", new_callable=AsyncMock) as mock_snap, \
             patch.object(_WebInspectorServiceImpl, "generate_selectors_for_element", new_callable=AsyncMock) as mock_gen:
            
            mock_snap.return_value = ({"tree": mock_tree}, None)
            mock_gen.return_value = {
                "success": True,
                "selectors": [{"name": "Class", "value": "button.delete-action", "count": 1}]
            }

            from app.services.web_inspector_service import _active_sessions
            _active_sessions["test-session-2"] = {"user_id": 1}

            try:
                res = await _WebInspectorServiceImpl.ai_analyze_full_tree_and_correct(
                    "test-session-2", failed_step, user_id=1
                )
                self.assertTrue(res.get("success"))
                self.assertEqual(res.get("matched_node", {}).get("id"), "lws-delete")
                self.assertEqual(res.get("suggested_selector"), "button.delete-action")
            finally:
                _active_sessions.pop("test-session-2", None)

    async def test_ai_autocorrect_llm_fallback_invoked_on_low_score(self):
        """Validates that LLM fallback is attempted when heuristic score is low."""
        mock_tree = [
            {
                "id": "lws-complex-1",
                "tagName": "DIV",
                "text": "Comprar Agora",
                "iconAttr": "",
                "placeholder": "",
                "nameAttr": "",
                "idAttr": "",
                "labelAttr": "",
                "selector": "div.cta-btn"
            }
        ]

        failed_step = {
            "name": "Efetuar aquisição do produto",
            "type": "click",
            "properties": {"selector": "#buy-btn-broken"}
        }

        with patch.object(_WebInspectorServiceImpl, "get_snapshot", new_callable=AsyncMock) as mock_snap, \
             patch.object(_WebInspectorServiceImpl, "generate_selectors_for_element", new_callable=AsyncMock) as mock_gen:

            mock_snap.return_value = ({"tree": mock_tree}, None)
            mock_gen.return_value = {
                "success": True,
                "selectors": [{"name": "Class", "value": "div.cta-btn", "count": 1}]
            }

            mock_db = MagicMock()
            mock_db_module.SessionLocal.return_value = mock_db
            mock_settings = MagicMock()
            mock_settings.ai_enabled = True
            mock_settings.ai_provider = "flow_ia"
            mock_settings.ai_api_key = "test-key"
            mock_db.query.return_value.filter.return_value.first.return_value = mock_settings

            # LLM returns matched element id
            mock_analysis_module.AnalysisService._call_llm.return_value = '{"chosen_id": "lws-complex-1", "confidence": 0.98, "reasoning": "Corresponde ao CTA de compra"}'
            mock_skill_module._robust_json_parse.side_effect = lambda t: {"chosen_id": "lws-complex-1", "confidence": 0.98, "reasoning": "Corresponde ao CTA de compra"}

            from app.services.web_inspector_service import _active_sessions
            _active_sessions["test-session-3"] = {"user_id": 99}

            try:
                res = await _WebInspectorServiceImpl.ai_analyze_full_tree_and_correct(
                    "test-session-3", failed_step, user_id=99
                )
                self.assertTrue(res.get("success"))
                self.assertEqual(res.get("matched_node", {}).get("id"), "lws-complex-1")
                self.assertTrue(res.get("used_llm"))
            finally:
                _active_sessions.pop("test-session-3", None)

class TestWebInspectorStepExecution(unittest.IsolatedAsyncioTestCase):
    """Test suite validating standard step execution (assert, wait, keypress) in interact."""

    async def test_interact_assert_visible_success(self):
        """Validates that assert visible succeeds when text is in page body."""
        from app.services.web_inspector_service import _active_sessions
        mock_page = MagicMock()
        mock_page.evaluate = AsyncMock(return_value="Seu Carrinho de Compras (1 item)")
        _active_sessions["test-step-sess"] = {"page": mock_page, "last_accessed": 0}

        try:
            action = {
                "type": "assert",
                "properties": {
                    "operator": "visible",
                    "value": "Carrinho de Compras",
                    "timeout": 2000
                }
            }
            res, _ = await _WebInspectorServiceImpl.interact("test-step-sess", action, return_snapshot=False)
            self.assertTrue(res.get("success"))
        finally:
            _active_sessions.pop("test-step-sess", None)

    async def test_interact_assert_visible_failure_raises(self):
        """Validates that assert visible raises ValueError when text is absent."""
        from app.services.web_inspector_service import _active_sessions
        mock_page = MagicMock()
        mock_page.evaluate = AsyncMock(return_value="Página de Produtos - Nenhum item")
        _active_sessions["test-step-sess"] = {"page": mock_page, "last_accessed": 0}

        try:
            action = {
                "type": "assert",
                "properties": {
                    "operator": "visible",
                    "value": "Finalizar Compra",
                    "timeout": 500
                }
            }
            with self.assertRaises(ValueError):
                await _WebInspectorServiceImpl.interact("test-step-sess", action, return_snapshot=False)
        finally:
            _active_sessions.pop("test-step-sess", None)

    async def test_interact_wait_step_sleeps(self):
        """Validates that wait step executes without error."""
        from app.services.web_inspector_service import _active_sessions
        mock_page = MagicMock()
        _active_sessions["test-step-sess"] = {"page": mock_page, "last_accessed": 0}

        try:
            action = {
                "type": "wait",
                "properties": {"value": 50}
            }
            res, _ = await _WebInspectorServiceImpl.interact("test-step-sess", action, return_snapshot=False)
            self.assertTrue(res.get("success"))
        finally:
            _active_sessions.pop("test-step-sess", None)

if __name__ == "__main__":
    unittest.main()

