# ECS + ALB Terraform

This stack provisions:

- VPC + 2 public subnets
- ALB + target group + listener
- ECS Fargate cluster/service/task definition
- IAM task execution role and runtime task role
- DynamoDB tables for sessions/registry/calls
- S3 media bucket
- ECR repository
- CloudWatch log group
- ECS autoscaling policy

## Usage

```bash
cd infra/aws/terraform
terraform init
terraform apply \
  -var="container_image=<aws_account>.dkr.ecr.us-east-1.amazonaws.com/ekaette-nova:<tag>"
```

Optional secrets:

```bash
terraform apply \
  -var="container_image=..." \
  -var='secret_arns={"AT_API_KEY"="arn:aws:secretsmanager:...:secret:at_api_key"}'
```

