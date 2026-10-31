locals {
  moderador_nome = "guardrails-moderador"
}

resource "aws_cloudwatch_log_group" "moderador" {
  name              = "/aws/lambda/${local.moderador_nome}"
  retention_in_days = 14
}

data "aws_iam_policy_document" "moderador_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "moderador_permissoes" {
  statement {
    sid       = "DetectarPii"
    actions   = ["comprehend:DetectPiiEntities"]
    resources = ["*"]
  }

  statement {
    sid       = "LerChaveOpenAi"
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.openai_api_key.arn]
  }

  statement {
    sid       = "EscreverLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.moderador.arn}:*"]
  }
}

resource "aws_iam_role" "moderador" {
  name               = "${local.moderador_nome}-role"
  assume_role_policy = data.aws_iam_policy_document.moderador_assume.json
}

resource "aws_iam_role_policy" "moderador" {
  name   = "${local.moderador_nome}-policy"
  role   = aws_iam_role.moderador.id
  policy = data.aws_iam_policy_document.moderador_permissoes.json
}

data "archive_file" "moderador" {
  type        = "zip"
  source_dir  = "${path.module}/../src/moderador"
  output_path = "${path.module}/.build/moderador.zip"
}

resource "aws_lambda_function" "moderador" {
  function_name = local.moderador_nome
  description   = "Censura PII e conteúdo impróprio em texto."
  role          = aws_iam_role.moderador.arn

  filename         = data.archive_file.moderador.output_path
  source_code_hash = data.archive_file.moderador.output_base64sha256

  runtime       = "python3.13"
  handler       = "app.handler"
  architectures = ["arm64"]
  memory_size   = 256
  timeout       = 15

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.moderador.name
  }

  environment {
    variables = {
      PARAMETRO_OPENAI = aws_ssm_parameter.openai_api_key.name
    }
  }
}
