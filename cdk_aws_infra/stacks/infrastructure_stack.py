from aws_cdk import (
    Stack,
    CfnOutput,
    RemovalPolicy,
    Duration,
    aws_ec2 as ec2,
    aws_ssm as ssm,
    aws_autoscaling as autoscaling,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_s3 as s3,
    aws_certificatemanager as acm,
)
from constructs import Construct

class InfrastructureStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # Parâmetros configuráveis
        arch = self.node.try_get_context("arch") or "ARM_64"
        expose_swagger_public = self.node.try_get_context("expose_swagger_public") 
        if expose_swagger_public is None:
            expose_swagger_public = True
        restrict_swagger_to_cidr = self.node.try_get_context("restrict_swagger_to_cidr")
        use_eip = self.node.try_get_context("use_eip")
        if use_eip is None:
            use_eip = False
        
        # S3 Bucket para armazenar docker-compose.yml e configs
        self.config_bucket = s3.Bucket(self, "AppConfigBucket",
            removal_policy=RemovalPolicy.RETAIN,
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        # VPC (mantendo configuração atual)
        self.vpc = ec2.Vpc(self, "AppVPC",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24
                )
            ]
        )

        # Security Group interno compartilhado (seguindo requisitos)
        self.internal_sg = ec2.SecurityGroup(self, "InternalSG",
            vpc=self.vpc,
            description="Internal security group for FastAPI and Gateway communication",
            allow_all_outbound=True
        )
        # Comunicação interna entre membros do SG
        self.internal_sg.add_ingress_rule(self.internal_sg, ec2.Port.tcp(8000), "Internal port 8000")
        self.internal_sg.add_ingress_rule(self.internal_sg, ec2.Port.tcp(3000), "Internal port 3000")
        self.internal_sg.add_ingress_rule(self.internal_sg, ec2.Port.all_icmp(), "Internal ICMP")
        
        # SSH para debug - permitir de qualquer lugar temporariamente
        self.internal_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(22), "SSH for debugging")

        # Key Pair para SSH (criar se não existir)
        self.key_pair = ec2.CfnKeyPair(self, "DebuggingKeyPair",
            key_name="cdk-aws-infra-debug-key",
            # Nota: A private key deve ser baixada manualmente do console AWS
            # ou recuperada via AWS CLI após a criação
        )

        # Security Group para ALB
        self.alb_sg = ec2.SecurityGroup(self, "SwaggerALBSG",
            vpc=self.vpc,
            description="Security group for Swagger ALB",
            allow_all_outbound=True
        )

        if expose_swagger_public:
            if restrict_swagger_to_cidr:
                self.alb_sg.add_ingress_rule(
                    ec2.Peer.ipv4(restrict_swagger_to_cidr), 
                    ec2.Port.tcp(80), 
                    "HTTP from restricted CIDR"
                )
                self.alb_sg.add_ingress_rule(
                    ec2.Peer.ipv4(restrict_swagger_to_cidr), 
                    ec2.Port.tcp(443), 
                    "HTTPS from restricted CIDR"
                )
            else:
                self.alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "HTTP")
                self.alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "HTTPS")

        # Permitir tráfego do ALB para instâncias
        self.internal_sg.add_ingress_rule(self.alb_sg, ec2.Port.tcp(8000), "ALB to FastAPI")
        self.internal_sg.add_ingress_rule(self.alb_sg, ec2.Port.tcp(3000), "ALB to Gateway")

        # IAM Role para instâncias EC2
        self.ec2_role = iam.Role(self, "EC2Role",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"),
            ]
        )

        # Permissões para S3 e SSM
        self.config_bucket.grant_read(self.ec2_role)
        
        self.ec2_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "ssm:GetParameter",
                "ssm:GetParameters",
                "ssm:GetParametersByPath",
                "secretsmanager:GetSecretValue",
                "secretsmanager:DescribeSecret"
            ],
            resources=[
                f"arn:aws:ssm:{self.region}:{self.account}:parameter/app/*",
                f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:app/*"
            ]
        ))

        # Instance Profile
        self.instance_profile = iam.InstanceProfile(self, "EC2InstanceProfile",
            role=self.ec2_role
        )

        # AMI baseada na arquitetura atual
        cpu_type = ec2.AmazonLinuxCpuType.ARM_64 if arch == "ARM_64" else ec2.AmazonLinuxCpuType.X86_64
        self.ami = ec2.AmazonLinuxImage(
            generation=ec2.AmazonLinuxGeneration.AMAZON_LINUX_2023,
            cpu_type=cpu_type
        )

        # UserData idempotente para FastAPI
        self.fastapi_user_data = ec2.UserData.for_linux()
        self.fastapi_user_data.add_commands(
            "set -eux",
            "exec > /var/log/user-data.log 2>&1",  # Log everything
            "echo 'Starting FastAPI setup at $(date)'",
            "sudo yum update -y",
            "curl -fsSL https://get.docker.com | sh",
            "sudo systemctl enable docker && sudo systemctl start docker",
            "sudo usermod -aG docker ec2-user",
            "mkdir -p /opt/app /opt/data",
            "# Install Docker Compose",
            "sudo curl -L \"https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)\" -o /usr/local/bin/docker-compose",
            "sudo chmod +x /usr/local/bin/docker-compose",
            "sudo ln -sf /usr/local/bin/docker-compose /usr/bin/docker-compose",
            "# Download configs from S3 with retry logic",
            f"echo 'Attempting to download from s3://{self.config_bucket.bucket_name}/fastapi/'",
            "for i in {1..5}; do",
            f"  if aws s3 cp s3://{self.config_bucket.bucket_name}/fastapi/docker-compose.yml /opt/app/; then",
            "    echo 'Docker compose downloaded successfully'",
            "    CONFIG_DOWNLOADED=true",
            "    break",
            "  else",
            "    echo \"Download attempt $i failed, retrying in 10s...\"",
            "    sleep 10",
            "  fi",
            "done",
            f"aws s3 cp s3://{self.config_bucket.bucket_name}/fastapi/.env /opt/app/ || echo 'Env file not found, using defaults'",
            "# Create default docker-compose.yml only if download failed",
            "if [ \"$CONFIG_DOWNLOADED\" != \"true\" ]; then",
            "  echo 'Creating default FastAPI configuration...'",
            "cat > /opt/app/docker-compose.yml << 'EOF'",
            "version: '3.8'",
            "services:",
            "  fastapi:",
            "    image: python:3.11-slim",
            "    ports:",
            "      - '8000:8000'",
            "    restart: always",
            "    command: >",
            "      sh -c \"pip install fastapi uvicorn && ",
            "             echo 'from fastapi import FastAPI; from fastapi.responses import RedirectResponse; app = FastAPI(title=\\\"FastAPI Demo\\\", docs_url=\\\"/docs\\\"); @app.get(\\\"/health\\\") def health(): return {\\\"status\\\": \\\"ok\\\", \\\"service\\\": \\\"fastapi\\\"}; @app.get(\\\"/docs\\\") def docs_redirect(): return {\\\"message\\\": \\\"FastAPI service running\\\", \\\"docs\\\": \\\"Available at /docs\\\"}; @app.get(\\\"/swagger/api/docs\\\") def swagger_docs(): return RedirectResponse(url=\\\"/docs\\\")' > main.py && ",
            "             uvicorn main:app --host 0.0.0.0 --port 8000\"",
            "EOF",
            "fi",
            "cd /opt/app",
            "echo 'Starting docker-compose at $(date)'",
            "docker-compose up -d",
            "echo 'Waiting for container to start...'",
            "sleep 15",
            "echo 'Container status:'",
            "docker-compose ps",
            "echo 'Container logs:'",
            "docker-compose logs",
            "# Test if service is responding",
            "echo 'Testing FastAPI service...'",
            "for i in {1..10}; do",
            "  if curl -f http://localhost:8000/docs >/dev/null 2>&1; then",
            "    echo 'FastAPI service is responding on /docs'",
            "    break",
            "  else",
            "    echo \"Service test attempt $i failed, retrying in 5s...\"",
            "    sleep 5",
            "  fi",
            "done",
            "echo 'FastAPI setup completed at $(date)'",
            "echo 'Testing service...'",
            "curl -f http://localhost:8000/health || echo 'Health check failed'",
            "curl -f http://localhost:8000/docs || echo 'Docs check failed'",
            "# Setup health check cron",
            "echo '*/2 * * * * cd /opt/app && docker-compose ps | grep -q fastapi.*Up || docker-compose up -d' | crontab -",
            "echo 'FastAPI setup completed at $(date)'"
        )

        # UserData idempotente para Gateway
        self.gateway_user_data = ec2.UserData.for_linux()
        self.gateway_user_data.add_commands(
            "exec > /var/log/user-data.log 2>&1",
            "echo 'Gateway user data started at $(date)'",
            "set -eux",
            "sudo yum update -y",
            "curl -fsSL https://get.docker.com | sh",
            "sudo systemctl enable docker && sudo systemctl start docker",
            "sudo usermod -aG docker ec2-user",
            "mkdir -p /opt/gateway /opt/data",
            "# Install Docker Compose",
            "sudo curl -L \"https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)\" -o /usr/local/bin/docker-compose",
            "sudo chmod +x /usr/local/bin/docker-compose",
            "sudo ln -sf /usr/local/bin/docker-compose /usr/bin/docker-compose",
            "# Download configs from S3 with retry logic",
            f"echo 'Attempting to download from s3://{self.config_bucket.bucket_name}/gateway/'",
            "for i in {1..5}; do",
            f"  if aws s3 cp s3://{self.config_bucket.bucket_name}/gateway/docker-compose.yml /opt/gateway/; then",
            "    echo 'Gateway docker compose downloaded successfully'",
            "    GATEWAY_CONFIG_DOWNLOADED=true",
            "    break",
            "  else",
            "    echo \"Gateway download attempt $i failed, retrying in 10s...\"",
            "    sleep 10",
            "  fi",
            "done",
            f"aws s3 cp s3://{self.config_bucket.bucket_name}/gateway/.env /opt/gateway/ || echo 'Gateway env file not found, using defaults'",
            "# Create default docker-compose.yml only if download failed",
            "if [ \"$GATEWAY_CONFIG_DOWNLOADED\" != \"true\" ]; then",
            "  echo 'Creating default Gateway configuration...'",
            "cat > /opt/gateway/docker-compose.yml << 'EOF'",
            "version: '3.8'",
            "services:",
            "  gateway:",
            "    image: node:18-alpine",
            "    ports:",
            "      - '3000:3000'",
            "    restart: always",
            "    command: >",
            "      sh -c \"npm install -g express swagger-ui-express && ",
            "             echo 'const express = require(\\\"express\\\"); const swaggerUi = require(\\\"swagger-ui-express\\\"); const app = express(); const swaggerDocument = { openapi: \\\"3.0.0\\\", info: { title: \\\"Gateway API\\\", version: \\\"1.0.0\\\" }, paths: { \\\"/health\\\": { get: { summary: \\\"Health check\\\", responses: { \\\"200\\\": { description: \\\"OK\\\" } } } } } }; app.use(\\\"/api-docs\\\", swaggerUi.serve, swaggerUi.setup(swaggerDocument)); app.use(\\\"/swagger/gw/api-docs\\\", swaggerUi.serve, swaggerUi.setup(swaggerDocument)); app.get(\\\"/health\\\", (req, res) => res.json({status: \\\"ok\\\", service: \\\"gateway\\\"})); app.listen(3000, \\\"0.0.0.0\\\", () => console.log(\\\"Gateway running on port 3000\\\"));' > server.js && ",
            "             node server.js\"",
            "EOF",
            "fi",
            "cd /opt/gateway",
            "echo 'Starting Docker Compose...'",
            "docker-compose up -d",
            "echo 'Waiting for Gateway container to start...'",
            "sleep 15",
            "echo 'Checking container status:'",
            "docker-compose ps",
            "echo 'Checking container logs:'",
            "docker-compose logs --tail=50",
            "echo 'Testing Gateway service locally:'",
            "for i in {1..10}; do",
            "  if curl -f http://localhost:3000/api-docs >/dev/null 2>&1; then",
            "    echo 'Gateway service is responding on /api-docs'",
            "    break",
            "  else",
            "    echo \"Gateway test attempt $i failed, retrying in 5s...\"",
            "    sleep 5",
            "  fi",
            "done",
            "# Setup health check cron",
            "echo '*/2 * * * * cd /opt/gateway && docker-compose ps | grep -q gateway.*Up || docker-compose up -d' | crontab -",
            "echo 'Gateway setup completed at $(date)'"
        )

        # Launch Template para FastAPI (mantendo t4g.micro)
        self.fastapi_lt = ec2.LaunchTemplate(self, "FastAPILT",
            machine_image=self.ami,
            instance_type=ec2.InstanceType("t4g.micro"),  # Mantendo tipo atual
            security_group=self.internal_sg,
            user_data=self.fastapi_user_data,
            role=self.ec2_role,
            key_pair=ec2.KeyPair.from_key_pair_name(self, "FastAPIKeyPair", self.key_pair.key_name),  # SSH key para debug
            detailed_monitoring=True,
        )

        # Launch Template para Gateway (mantendo t4g.medium)
        self.gateway_lt = ec2.LaunchTemplate(self, "GatewayLT",
            machine_image=self.ami,
            instance_type=ec2.InstanceType("t4g.medium"),  # Mantendo tipo atual
            security_group=self.internal_sg,
            user_data=self.gateway_user_data,
            role=self.ec2_role,
            key_pair=ec2.KeyPair.from_key_pair_name(self, "GatewayKeyPair", self.key_pair.key_name),  # SSH key para debug
            detailed_monitoring=True,
        )

        # Auto Scaling Group para FastAPI (capacidade 1, controle manual de atualizações)
        self.fastapi_asg = autoscaling.AutoScalingGroup(self, "FastAPIASG",
            vpc=self.vpc,
            launch_template=self.fastapi_lt,
            min_capacity=1,
            max_capacity=1,
            desired_capacity=1,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            health_check=autoscaling.HealthCheck.elb(grace=Duration.seconds(300)),
        )

        # Auto Scaling Group para Gateway (capacidade 1, controle manual de atualizações)
        self.gateway_asg = autoscaling.AutoScalingGroup(self, "GatewayASG",
            vpc=self.vpc,
            launch_template=self.gateway_lt,
            min_capacity=1,
            max_capacity=1,
            desired_capacity=1,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            health_check=autoscaling.HealthCheck.elb(grace=Duration.seconds(300)),
        )

        # Configurar políticas de retenção para ASGs
        self.fastapi_asg.apply_removal_policy(RemovalPolicy.RETAIN)
        self.gateway_asg.apply_removal_policy(RemovalPolicy.RETAIN)

        # Application Load Balancer para Swagger
        self.swagger_alb = elbv2.ApplicationLoadBalancer(self, "SwaggerALB",
            vpc=self.vpc,
            internet_facing=expose_swagger_public,
            security_group=self.alb_sg,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC)
        )

        # Target Groups
        self.fastapi_tg = elbv2.ApplicationTargetGroup(self, "FastAPITG",
            vpc=self.vpc,
            port=8000,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.INSTANCE,
            health_check=elbv2.HealthCheck(
                path="/docs",  # FastAPI Swagger endpoint real
                healthy_http_codes="200",
                interval=Duration.seconds(30),
                timeout=Duration.seconds(10),
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
            ),
            deregistration_delay=Duration.seconds(30),
        )

        self.gateway_tg = elbv2.ApplicationTargetGroup(self, "GatewayTG",
            vpc=self.vpc,
            port=3000,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.INSTANCE,
            health_check=elbv2.HealthCheck(
                path="/api-docs",  # Gateway Swagger endpoint real
                healthy_http_codes="200",
                interval=Duration.seconds(30),
                timeout=Duration.seconds(10),
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
            ),
            deregistration_delay=Duration.seconds(30),
        )

        # Registrar ASGs nos Target Groups
        self.fastapi_asg.attach_to_application_target_group(self.fastapi_tg)
        self.gateway_asg.attach_to_application_target_group(self.gateway_tg)

        # ALB Listener
        self.alb_listener = self.swagger_alb.add_listener("HttpListener",
            port=80,
            open=expose_swagger_public,
            default_action=elbv2.ListenerAction.fixed_response(404, 
                content_type="text/plain", 
                message_body="Not Found"
            )
        )

        # Path-based routing
        self.alb_listener.add_action("FastAPIRule",
            priority=10,
            conditions=[elbv2.ListenerCondition.path_patterns(["/swagger/api/*"])],
            action=elbv2.ListenerAction.forward([self.fastapi_tg])
        )

        self.alb_listener.add_action("GatewayRule",
            priority=20,
            conditions=[elbv2.ListenerCondition.path_patterns(["/swagger/gw/*"])],
            action=elbv2.ListenerAction.forward([self.gateway_tg])
        )

        # SSM State Manager Document para auto-heal
        self.state_manager_doc = ssm.CfnDocument(self, "AppStateManagerDoc",
            document_type="Command",
            document_format="YAML",
            content={
                "schemaVersion": "2.2",
                "description": "Ensure Docker services are running",
                "mainSteps": [
                    {
                        "action": "aws:runShellScript",
                        "name": "ensureDockerServices",
                        "inputs": {
                            "runCommand": [
                                "#!/bin/bash",
                                "set -e",
                                "# Check and restart Docker if needed",
                                "systemctl is-active docker || systemctl start docker",
                                "# Check FastAPI service",
                                "if [ -d /opt/app ]; then",
                                "  cd /opt/app",
                                "  docker-compose ps | grep -q 'Up' || docker-compose up -d",
                                "fi",
                                "# Check Gateway service", 
                                "if [ -d /opt/gateway ]; then",
                                "  cd /opt/gateway",
                                "  docker-compose ps | grep -q 'Up' || docker-compose up -d",
                                "fi"
                            ]
                        }
                    }
                ]
            }
        )

        # Elastic IPs (se solicitado)
        if use_eip:
            self.fastapi_eip = ec2.CfnEIP(self, "FastAPIEIP",
                domain="vpc"
            )
            self.gateway_eip = ec2.CfnEIP(self, "GatewayEIP", 
                domain="vpc"
            )

        # Outputs necessários
        CfnOutput(self, "VpcId",
            value=self.vpc.vpc_id,
            description="VPC ID"
        )

        CfnOutput(self, "InternalSGId",
            value=self.internal_sg.security_group_id,
            description="Internal Security Group ID"
        )

        CfnOutput(self, "ALBSGId",
            value=self.alb_sg.security_group_id,
            description="ALB Security Group ID"
        )

        CfnOutput(self, "SwaggerAlbDnsName",
            value=self.swagger_alb.load_balancer_dns_name,
            description="Swagger ALB DNS Name"
        )

        CfnOutput(self, "SwaggerAlbUrl",
            value=f"http://{self.swagger_alb.load_balancer_dns_name}",
            description="Swagger ALB URL"
        )

        CfnOutput(self, "FastAPISwaggerUrl",
            value=f"http://{self.swagger_alb.load_balancer_dns_name}/swagger/api/docs",
            description="FastAPI Swagger URL (via ALB - /docs endpoint)"
        )

        CfnOutput(self, "GatewaySwaggerUrl",
            value=f"http://{self.swagger_alb.load_balancer_dns_name}/swagger/gw/api-docs",
            description="Gateway Swagger URL (via ALB - /api-docs endpoint)"
        )

        CfnOutput(self, "FastAPITargetGroupArn",
            value=self.fastapi_tg.target_group_arn,
            description="FastAPI Target Group ARN"
        )

        CfnOutput(self, "GatewayTargetGroupArn", 
            value=self.gateway_tg.target_group_arn,
            description="Gateway Target Group ARN"
        )

        CfnOutput(self, "ALBListenerArn",
            value=self.alb_listener.listener_arn,
            description="ALB Listener ARN"
        )

        CfnOutput(self, "ConfigBucketName",
            value=self.config_bucket.bucket_name,
            description="S3 Bucket for app configurations"
        )

        CfnOutput(self, "FastAPIASGName",
            value=self.fastapi_asg.auto_scaling_group_name,
            description="FastAPI Auto Scaling Group Name"
        )

        CfnOutput(self, "GatewayASGName",
            value=self.gateway_asg.auto_scaling_group_name,
            description="Gateway Auto Scaling Group Name"
        )
        
        # SSH Debug Outputs
        CfnOutput(self, "SSHKeyName",
            value=self.key_pair.key_name,
            description="SSH Key Pair Name for debugging instances"
        )
        
        CfnOutput(self, "SSHInstructions",
            value="Para SSH: 1) Baixe a private key no console AWS EC2 > Key Pairs > cdk-aws-infra-debug-key, 2) Salve como cdk-aws-infra-debug-key.pem, 3) Execute './deploy.sh ssh fastapi'",
            description="Instructions for SSH access to instances"
        )

        if use_eip:
            CfnOutput(self, "FastAPIEIPAllocationId",
                value=self.fastapi_eip.attr_allocation_id,
                description="FastAPI EIP Allocation ID"
            )
            CfnOutput(self, "GatewayEIPAllocationId",
                value=self.gateway_eip.attr_allocation_id,
                description="Gateway EIP Allocation ID"
            )

        # ===================== SSM PARAMETERS (para consumo externo) =====================
        # Cria parâmetros padronizados em /infra/cdk/* usados pelos pipelines dos serviços
        self.param_config_bucket = ssm.StringParameter(self, "ParamConfigBucket",
            parameter_name="/infra/cdk/config/bucket",
            string_value=self.config_bucket.bucket_name
        )
        self.param_alb_dns = ssm.StringParameter(self, "ParamAlbDns",
            parameter_name="/infra/cdk/alb/dns",
            string_value=self.swagger_alb.load_balancer_dns_name
        )
        self.param_fastapi_asg = ssm.StringParameter(self, "ParamFastapiAsg",
            parameter_name="/infra/cdk/fastapi/asg-name",
            string_value=self.fastapi_asg.auto_scaling_group_name
        )
        self.param_gateway_asg = ssm.StringParameter(self, "ParamGatewayAsg",
            parameter_name="/infra/cdk/gateway/asg-name",
            string_value=self.gateway_asg.auto_scaling_group_name
        )
        self.param_fastapi_tg = ssm.StringParameter(self, "ParamFastapiTg",
            parameter_name="/infra/cdk/fastapi/target-group-arn",
            string_value=self.fastapi_tg.target_group_arn
        )
        self.param_gateway_tg = ssm.StringParameter(self, "ParamGatewayTg",
            parameter_name="/infra/cdk/gateway/target-group-arn",
            string_value=self.gateway_tg.target_group_arn
        )
        self.param_internal_sg = ssm.StringParameter(self, "ParamInternalSg",
            parameter_name="/infra/cdk/security/internal-sg-id",
            string_value=self.internal_sg.security_group_id
        )
        self.param_alb_sg = ssm.StringParameter(self, "ParamAlbSg",
            parameter_name="/infra/cdk/security/alb-sg-id",
            string_value=self.alb_sg.security_group_id
        )
        self.param_ssh_key = ssm.StringParameter(self, "ParamSshKeyName",
            parameter_name="/infra/cdk/ssh/key-name",
            string_value=self.key_pair.key_name
        )
        if use_eip:
            self.param_fastapi_eip = ssm.StringParameter(self, "ParamFastapiEip",
                parameter_name="/infra/cdk/eip/fastapi",
                string_value=self.fastapi_eip.attr_allocation_id
            )
            self.param_gateway_eip = ssm.StringParameter(self, "ParamGatewayEip",
                parameter_name="/infra/cdk/eip/gateway",
                string_value=self.gateway_eip.attr_allocation_id
            )
        # =================== FIM SSM PARAMETERS ===================