# Kill switch: zera a concorrência da Lambda principal. Não lê o evento.
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    function_name = os.environ["TARGET_FUNCTION"]
    boto3.client("lambda").put_function_concurrency(
        FunctionName=function_name,
        ReservedConcurrentExecutions=0,
    )
    logger.info("kill switch: concurrency of %s set to 0", function_name)
