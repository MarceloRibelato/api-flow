import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

class QueueExecutorService:
    @staticmethod
    def execute_queue_step(
        step: Dict[str, Any],
        variables: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Executa um step de mensageria: Publicar (publish) ou Consumir (consume).
        """
        from app.services.variable_extractor_service import VariableExtractorService
        from app.services.flow_executor_service import FlowExecutorService
        import time

        broker = step.get('broker', 'rabbitmq')
        action = step.get('action', 'publish')
        connection_string_template = step.get('connectionString', '')
        queue_name_template = step.get('queueName', '')
        payload_template = step.get('payload', '')
        timeout = int(step.get('timeout', 30000))
        assertions = step.get('assertions', [])
        extracts = step.get('extracts', [])

        # Interpolação
        conn_str = VariableExtractorService.interpolate_string(connection_string_template, variables)
        queue_name = VariableExtractorService.interpolate_string(queue_name_template, variables)

        # Tratar Payload (pode ser dict ou string)
        if isinstance(payload_template, dict):
            payload_str = json.dumps(payload_template)
            payload_str = VariableExtractorService.interpolate_string(payload_str, variables)
        else:
            payload_str = VariableExtractorService.interpolate_string(str(payload_template or ""), variables)

        start_time = time.time()
        result = {
            "step_id": step.get('id'),
            "name": step.get('name'),
            "broker": broker,
            "action": action,
            "queue": queue_name,
            "status": 500,
            "error": None,
            "response_time": 0,
            "message": None
        }

        try:
            if broker == 'rabbitmq':
                res = QueueExecutorService._execute_rabbitmq(action, conn_str, queue_name, payload_str, timeout)
            elif broker == 'sqs':
                res = QueueExecutorService._execute_sqs(action, conn_str, queue_name, payload_str, timeout)
            elif broker == 'service_bus':
                res = QueueExecutorService._execute_service_bus(action, conn_str, queue_name, payload_str, timeout)
            else:
                raise ValueError(f"Broker não suportado: {broker}")

            result.update(res)

            # Assertions / Extracts para Consumer
            if action == 'consume' and result.get('status') == 200:
                msg_dict = {}
                try:
                    if isinstance(result.get('message'), str):
                        msg_dict = json.loads(result.get('message'))
                    elif isinstance(result.get('message'), dict):
                        msg_dict = result.get('message')
                except Exception:
                    msg_dict = {"raw": result.get('message')}

                # Mapeia para o formato que o FlowExecutorService entende
                pseudo_response = {
                    "status_code": result.get('status'),
                    "body": msg_dict,
                    "headers": {}
                }

                extracted = FlowExecutorService.extract_variables(pseudo_response, extracts)
                variables.update(extracted)
                result['extracted'] = extracted

                assertion_results = FlowExecutorService.evaluate_assertions(pseudo_response, assertions)
                result['assertion_results'] = assertion_results
                result['assertions_passed'] = all(a['passed'] for a in assertion_results) if assertion_results else True
            else:
                result['assertions_passed'] = True # Publish sempre passa se não deu erro

        except Exception as e:
            logger.error(f"Erro no QueueExecutor ({broker} - {action}): {e}")
            result['error'] = str(e)
            result['assertions_passed'] = False

        result['response_time'] = int((time.time() - start_time) * 1000)
        return result

    @staticmethod
    def _execute_rabbitmq(action: str, conn_str: str, queue_name: str, payload: str, timeout_ms: int):
        try:
            import pika
        except ImportError:
            return {"status": 500, "error": "Pacote 'pika' não instalado no backend."}
        
        try:
            params = pika.URLParameters(conn_str)
            connection = pika.BlockingConnection(params)
            channel = connection.channel()
            channel.queue_declare(queue=queue_name, durable=True)

            if action == 'publish':
                channel.basic_publish(
                    exchange='',
                    routing_key=queue_name,
                    body=payload,
                    properties=pika.BasicProperties(delivery_mode=2) # Persistent
                )
                connection.close()
                return {"status": 200, "message": "Mensagem publicada com sucesso"}
            
            elif action == 'consume':
                # Consumir apenas 1 mensagem
                timeout_sec = timeout_ms / 1000.0
                method_frame, header_frame, body = next(channel.consume(queue=queue_name, inactivity_timeout=timeout_sec))
                if method_frame:
                    channel.basic_ack(method_frame.delivery_tag)
                    connection.close()
                    return {"status": 200, "message": body.decode('utf-8') if body else None}
                else:
                    connection.close()
                    return {"status": 404, "error": "Timeout: Nenhuma mensagem encontrada"}
        except Exception as e:
            return {"status": 500, "error": str(e)}

    @staticmethod
    def _execute_sqs(action: str, queue_url: str, queue_name: str, payload: str, timeout_ms: int):
        try:
            import boto3
        except ImportError:
            return {"status": 500, "error": "Pacote 'boto3' não instalado no backend."}
        
        # Para AWS SQS, a conn_str costuma ser a URL da Fila ou um json com chaves.
        # Simplificaremos assumindo que as credenciais AWS_ACCESS_KEY_ID estão no ambiente
        # ou passadas de outra forma, e queue_url contém a URL.
        try:
            # Em um ambiente real, deve-se extrair as credenciais
            sqs = boto3.client('sqs')
            
            if action == 'publish':
                response = sqs.send_message(
                    QueueUrl=queue_url,
                    MessageBody=payload
                )
                return {"status": 200, "message": f"Message ID: {response.get('MessageId')}"}
            
            elif action == 'consume':
                response = sqs.receive_message(
                    QueueUrl=queue_url,
                    MaxNumberOfMessages=1,
                    WaitTimeSeconds=min(20, int(timeout_ms/1000)) # SQS Long Polling max is 20s
                )
                messages = response.get('Messages', [])
                if messages:
                    message = messages[0]
                    receipt_handle = message['ReceiptHandle']
                    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
                    return {"status": 200, "message": message['Body']}
                return {"status": 404, "error": "Timeout: Nenhuma mensagem encontrada"}
        except Exception as e:
            return {"status": 500, "error": str(e)}

    @staticmethod
    def _execute_service_bus(action: str, conn_str: str, queue_name: str, payload: str, timeout_ms: int):
        try:
            from azure.servicebus import ServiceBusClient, ServiceBusMessage
        except ImportError:
            return {"status": 500, "error": "Pacote 'azure-servicebus' não instalado no backend."}
        
        try:
            client = ServiceBusClient.from_connection_string(conn_str)
            if action == 'publish':
                with client:
                    sender = client.get_queue_sender(queue_name=queue_name)
                    with sender:
                        message = ServiceBusMessage(payload)
                        sender.send_messages(message)
                return {"status": 200, "message": "Mensagem publicada com sucesso"}
            
            elif action == 'consume':
                with client:
                    receiver = client.get_queue_receiver(queue_name=queue_name, max_wait_time=int(timeout_ms/1000))
                    with receiver:
                        for msg in receiver:
                            body = str(msg)
                            receiver.complete_message(msg)
                            return {"status": 200, "message": body}
                return {"status": 404, "error": "Timeout: Nenhuma mensagem encontrada"}
        except Exception as e:
            return {"status": 500, "error": str(e)}
