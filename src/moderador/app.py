# Placeholder até a etapa 7: falha fechada, sem ler o evento.
import json


def handler(event, context):
    return {
        "statusCode": 502,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {"erro": "serviço de moderação indisponível"}, ensure_ascii=False
        ),
    }
