# ── ECR Repository ────────────────────────────────────────────────────────────

resource "aws_ecr_repository" "this" {
  name                 = local.name
  image_tag_mutability = "MUTABLE"
  force_delete         = true
  tags                 = local.tags

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  repository = aws_ecr_repository.this.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 5 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = { type = "expire" }
    }]
  })
}

# ── Docker build + push ───────────────────────────────────────────────────────
# Re-runs whenever main.py, requirements.txt, or the Dockerfile changes.

resource "null_resource" "docker_build_push" {
  triggers = {
    source       = filemd5("${path.module}/../server/main.py")
    requirements = filemd5("${path.module}/../server/requirements.txt")
    dockerfile   = filemd5("${path.module}/../server/Dockerfile")
    platform     = var.docker_platform
  }

  provisioner "local-exec" {
    command = <<-SHELL
      set -e
      aws ecr get-login-password --region ${var.aws_region} | \
        docker login --username AWS --password-stdin ${aws_ecr_repository.this.repository_url}
      docker build --platform ${var.docker_platform} \
        -t ${aws_ecr_repository.this.repository_url}:latest \
        ${path.module}/../server/
      docker push ${aws_ecr_repository.this.repository_url}:latest
    SHELL
  }

  depends_on = [aws_ecr_repository.this]
}
