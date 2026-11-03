resource "aws_sns_topic" "alertas" {
  name = "guardrails-alertas"
}

resource "aws_sns_topic_subscription" "alertas_email" {
  topic_arn = aws_sns_topic.alertas.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

locals {
  kill_switch_nome = "guardrails-kill-switch"
}

resource "aws_cloudwatch_log_group" "kill_switch" {
  name              = "/aws/lambda/${local.kill_switch_nome}"
  retention_in_days = 14
}

data "aws_iam_policy_document" "kill_switch_permissoes" {
  statement {
    sid       = "ZerarConcorrencia"
    actions   = ["lambda:PutFunctionConcurrency"]
    resources = [aws_lambda_function.moderador.arn]
  }

  statement {
    sid       = "EscreverLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.kill_switch.arn}:*"]
  }
}

resource "aws_iam_role" "kill_switch" {
  name               = "${local.kill_switch_nome}-role"
  assume_role_policy = data.aws_iam_policy_document.moderador_assume.json
}

resource "aws_iam_role_policy" "kill_switch" {
  name   = "${local.kill_switch_nome}-policy"
  role   = aws_iam_role.kill_switch.id
  policy = data.aws_iam_policy_document.kill_switch_permissoes.json
}

data "archive_file" "kill_switch" {
  type        = "zip"
  source_dir  = "${path.module}/../src/kill_switch"
  output_path = "${path.module}/.build/kill_switch.zip"
}

resource "aws_lambda_function" "kill_switch" {
  function_name = local.kill_switch_nome
  description   = "Zera a concorrência da Lambda principal."
  role          = aws_iam_role.kill_switch.arn

  filename         = data.archive_file.kill_switch.output_path
  source_code_hash = data.archive_file.kill_switch.output_base64sha256

  runtime       = "python3.13"
  handler       = "app.handler"
  architectures = ["arm64"]
  memory_size   = 128
  timeout       = 10

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.kill_switch.name
  }

  environment {
    variables = {
      FUNCAO_ALVO = aws_lambda_function.moderador.function_name
    }
  }
}

resource "aws_lambda_permission" "kill_switch_sns" {
  statement_id  = "PermitirSnsAlertas"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.kill_switch.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.alertas.arn
}

resource "aws_sns_topic_subscription" "alertas_kill_switch" {
  topic_arn = aws_sns_topic.alertas.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.kill_switch.arn
}
