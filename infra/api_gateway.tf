locals {
  api_nome    = "guardrails-moderador-api"
  api_caminho = "moderar"
  api_stage   = "v1"

  api_erros = {
    BAD_REQUEST_BODY             = { status = "400", mensagem = "requisição inválida" }
    INVALID_API_KEY              = { status = "403", mensagem = "acesso negado" }
    MISSING_AUTHENTICATION_TOKEN = { status = "403", mensagem = "acesso negado" }
    THROTTLED                    = { status = "429", mensagem = "limite de requisições excedido" }
    QUOTA_EXCEEDED               = { status = "429", mensagem = "limite de requisições excedido" }
    DEFAULT_4XX                  = { status = null, mensagem = "requisição recusada" }
    DEFAULT_5XX                  = { status = null, mensagem = "serviço de moderação indisponível" }
  }
}

resource "aws_api_gateway_rest_api" "moderador" {
  name        = local.api_nome
  description = "API de moderação de texto."

  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

resource "aws_api_gateway_resource" "moderar" {
  rest_api_id = aws_api_gateway_rest_api.moderador.id
  parent_id   = aws_api_gateway_rest_api.moderador.root_resource_id
  path_part   = local.api_caminho
}

resource "aws_api_gateway_model" "moderar" {
  rest_api_id  = aws_api_gateway_rest_api.moderador.id
  name         = "ModerarRequest"
  content_type = "application/json"

  schema = jsonencode({
    "$schema" = "http://json-schema.org/draft-04/schema#"
    title     = "ModerarRequest"
    type      = "object"
    required  = ["texto", "modo"]
    properties = {
      texto = {
        type      = "string"
        minLength = 1
        maxLength = 1000
      }
      modo = {
        type = "integer"
        enum = [1, 2, 3]
      }
    }
  })
}

resource "aws_api_gateway_request_validator" "corpo" {
  rest_api_id           = aws_api_gateway_rest_api.moderador.id
  name                  = "validar-corpo"
  validate_request_body = true
}

resource "aws_api_gateway_method" "moderar_post" {
  rest_api_id      = aws_api_gateway_rest_api.moderador.id
  resource_id      = aws_api_gateway_resource.moderar.id
  http_method      = "POST"
  authorization    = "NONE"
  api_key_required = true

  request_validator_id = aws_api_gateway_request_validator.corpo.id
  request_models = {
    "application/json" = aws_api_gateway_model.moderar.name
  }
}

resource "aws_api_gateway_integration" "moderar_post" {
  rest_api_id             = aws_api_gateway_rest_api.moderador.id
  resource_id             = aws_api_gateway_resource.moderar.id
  http_method             = aws_api_gateway_method.moderar_post.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.moderador.invoke_arn
}

resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "PermitirApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.moderador.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.moderador.execution_arn}/${local.api_stage}/POST/${local.api_caminho}"
}

resource "aws_api_gateway_gateway_response" "erros" {
  for_each = local.api_erros

  rest_api_id   = aws_api_gateway_rest_api.moderador.id
  response_type = each.key
  status_code   = each.value.status

  response_templates = {
    "application/json" = jsonencode({ erro = each.value.mensagem })
  }
}

resource "aws_api_gateway_deployment" "moderador" {
  rest_api_id = aws_api_gateway_rest_api.moderador.id

  triggers = {
    redeploy = filesha1("${path.module}/api_gateway.tf")
  }

  depends_on = [
    aws_api_gateway_integration.moderar_post,
    aws_api_gateway_gateway_response.erros,
  ]

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "moderador" {
  rest_api_id   = aws_api_gateway_rest_api.moderador.id
  deployment_id = aws_api_gateway_deployment.moderador.id
  stage_name    = local.api_stage
}

resource "aws_api_gateway_api_key" "demo" {
  name = "guardrails-moderador-key"
}

resource "aws_api_gateway_usage_plan" "moderador" {
  name = "guardrails-moderador-plano"

  api_stages {
    api_id = aws_api_gateway_rest_api.moderador.id
    stage  = aws_api_gateway_stage.moderador.stage_name
  }

  throttle_settings {
    rate_limit  = 5
    burst_limit = 10
  }

  quota_settings {
    limit  = 1000
    period = "DAY"
  }
}

resource "aws_api_gateway_usage_plan_key" "demo" {
  usage_plan_id = aws_api_gateway_usage_plan.moderador.id
  key_id        = aws_api_gateway_api_key.demo.id
  key_type      = "API_KEY"
}
