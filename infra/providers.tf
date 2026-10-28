provider "aws" {
  region = "us-east-1"

  default_tags {
    tags = {
      Projeto    = "guardrails-moderator"
      Gerenciado = "terraform"
    }
  }
}
