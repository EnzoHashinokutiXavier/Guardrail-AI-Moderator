resource "aws_ssm_parameter" "openai_api_key" {
  name        = "guardrails-openai-api-key"
  description = "Chave da API da OpenAI usada pela Lambda principal."
  type        = "SecureString"
  key_id      = "alias/aws/ssm"
  tier        = "Standard"

  value_wo         = var.openai_api_key
  value_wo_version = 1
}
