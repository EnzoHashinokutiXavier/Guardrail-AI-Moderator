# Kill switch: zera a concorrência da Lambda principal. Não lê o evento.
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    funcao = os.environ["FUNCAO_ALVO"]
    boto3.client("lambda").put_function_concurrency(
        FunctionName=funcao,
        ReservedConcurrentExecutions=0,
    )
    logger.info("kill switch: concorrência de %s zerada", funcao)
