import httpx
from sqlalchemy.orm import Session
from app.models.integration_models import ProjectIntegrationDB
from typing import Dict, Any

class IntegrationService:
    @staticmethod
    def get_project_integration(db: Session, product_id: int, provider: str) -> Dict[str, Any]:
        integration = db.query(ProjectIntegrationDB).filter(
            ProjectIntegrationDB.product_id == product_id,
            ProjectIntegrationDB.provider == provider
        ).first()
        
        if not integration:
            return None
            
        return integration.config

    @staticmethod
    def save_project_integration(db: Session, product_id: int, provider: str, config: Dict[str, Any]):
        integration = db.query(ProjectIntegrationDB).filter(
            ProjectIntegrationDB.product_id == product_id,
            ProjectIntegrationDB.provider == provider
        ).first()
        
        if integration:
            integration.config = config
        else:
            integration = ProjectIntegrationDB(
                product_id=product_id,
                provider=provider,
                config=config
            )
            db.add(integration)
            
        db.commit()
        return integration.config

    @staticmethod
    def delete_project_integration(db: Session, product_id: int, provider: str):
        integration = db.query(ProjectIntegrationDB).filter(
            ProjectIntegrationDB.product_id == product_id,
            ProjectIntegrationDB.provider == provider
        ).first()
        
        if integration:
            db.delete(integration)
            db.commit()
            return True
        return False

    @staticmethod
    async def create_issue(db: Session, product_id: int, provider: str, payload: Dict[str, Any]):
        config = IntegrationService.get_project_integration(db, product_id, provider)
        if not config:
            raise Exception(f"Integração {provider} não configurada para este projeto.")

        if provider == 'jira':
            return await IntegrationService._create_jira_issue(config, payload)
        elif provider == 'azure':
            return await IntegrationService._create_azure_issue(config, payload)
        else:
            raise Exception("Provedor não suportado.")

    @staticmethod
    async def _create_jira_issue(config: Dict[str, Any], payload: Dict[str, Any]):
        url = config.get("url", "").rstrip("/")
        email = config.get("email")
        token = config.get("token")
        project_key = config.get("project_key")
        
        if not url or not email or not token or not project_key:
            raise Exception("Configuração do Jira incompleta (necessita url, email, token e project_key).")

        summary = payload.get("summary", "Falha de Execução no Flow")
        description = payload.get("description", "Sem detalhes.")
        parent_task = payload.get("parent_task")

        jira_payload = {
            "fields": {
                "project": {
                    "key": project_key
                },
                "summary": summary,
                "description": description,
                "issuetype": {
                    "name": "Bug"
                }
            }
        }
        
        # If parent task is provided, we can link it. For Jira, it can be 'parent' or just 'issuelinks'.
        # We will try to add it as parent, but if the issue type doesn't support parent, Jira might complain.
        if parent_task:
             # Basic way to link to parent (e.g. Sub-task) or just link it via issueLinks.
             # For simplicity in MVP, we just mention it in description if complex.
             jira_payload["fields"]["description"] += f"\n\nTask Referência: {parent_task}"

        endpoint = f"{url}/rest/api/2/issue"
        auth = (email, token)

        async with httpx.AsyncClient() as client:
            response = await client.post(endpoint, json=jira_payload, auth=auth)
            
            if response.status_code >= 400:
                raise Exception(f"Erro no Jira: {response.text}")
                
            data = response.json()
            return {
                "id": data.get("id"),
                "key": data.get("key"),
                "url": f"{url}/browse/{data.get('key')}"
            }

    @staticmethod
    async def _create_azure_issue(config: Dict[str, Any], payload: Dict[str, Any]):
        # Placeholder for Azure implementation
        # Azure usually requires Organization, Project, and a PAT.
        url = config.get("url", "").rstrip("/") # https://dev.azure.com/{org}/{project}
        token = config.get("token") # PAT
        
        if not url or not token:
            raise Exception("Configuração do Azure incompleta (necessita url e token).")
            
        summary = payload.get("summary", "Falha de Execução no Flow")
        description = payload.get("description", "Sem detalhes.")

        azure_payload = [
            {
                "op": "add",
                "path": "/fields/System.Title",
                "value": summary
            },
            {
                "op": "add",
                "path": "/fields/System.Description",
                "value": description
            }
        ]
        
        endpoint = f"{url}/_apis/wit/workitems/$Bug?api-version=6.0"
        auth = ("", token) # Azure PAT is used as password with empty username

        async with httpx.AsyncClient() as client:
            response = await client.post(
                endpoint, 
                json=azure_payload, 
                auth=auth,
                headers={"Content-Type": "application/json-patch+json"}
            )
            
            if response.status_code >= 400:
                raise Exception(f"Erro no Azure: {response.text}")
                
            data = response.json()
            return {
                "id": data.get("id"),
                "url": data.get("_links", {}).get("html", {}).get("href", f"{url}/_workitems/edit/{data.get('id')}")
            }
