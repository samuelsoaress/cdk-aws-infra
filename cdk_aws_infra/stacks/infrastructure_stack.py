from aws_cdk import (
    Stack,
    CfnOutput,
    RemovalPolicy,
    aws_ec2 as ec2,
    aws_ssm as ssm,
    aws_iam as iam,
    aws_s3 as s3,
    aws_elasticloadbalancingv2 as elbv2,
)
from constructs import Construct


class InfrastructureStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # Context / flags
        # Context / flags (retorno ao modo 2 instâncias + ALB, sem ASG)
        arch = self.node.try_get_context("arch") or "ARM_64"
        expose_swagger_public = self.node.try_get_context("expose_swagger_public")
        if expose_swagger_public is None:
            expose_swagger_public = True
        restrict_swagger_to_cidr = self.node.try_get_context("restrict_swagger_to_cidr")
        ssh_key_name = self.node.try_get_context("ssh_key_name") or "cdk-aws-infra-debug-key"
        reuse_ssh_key = bool(self.node.try_get_context("reuse_ssh_key"))  # -c reuse_ssh_key=true para apenas referenciar
        persistent_mode = False  # agora sempre duas instâncias fixas

        # Bucket para configs
        self.config_bucket = s3.Bucket(self, "AppConfigBucket",
            removal_policy=RemovalPolicy.RETAIN,
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        # VPC simples pública
        self.vpc = ec2.Vpc(self, "AppVPC",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24)]
        )

        # SG interno (e também usado em dev mode)
        self.internal_sg = ec2.SecurityGroup(self, "InternalSG",
            vpc=self.vpc,
            description="Internal SG for services",
            allow_all_outbound=True
        )
        for p in (8000, 3000):
            self.internal_sg.add_ingress_rule(self.internal_sg, ec2.Port.tcp(p), f"Internal port {p}")
        self.internal_sg.add_ingress_rule(self.internal_sg, ec2.Port.all_icmp(), "Internal ICMP")
        self.internal_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(22), "SSH")

        # KeyPair (customizável)
        # Problema anterior: CREATE_FAILED quando o nome fixo já existia em outra stack.
        # Estratégia:
        #   - Por padrão (reuse_ssh_key=True) apenas referenciamos um KeyPair existente no console/ec2 (não criamos recurso CloudFormation).
        #   - Se reuse_ssh_key=False criamos um novo KeyPair com sufixo único (addr) para evitar colisão de nomes.
        base_key_name = ssh_key_name
        if reuse_ssh_key:
            key_name = base_key_name
            self.key_pair_resource = None  # Nenhuma criação -> precisa existir previamente.
        else:
            unique_suffix = self.node.addr[:8]
            generated_key_name = f"{base_key_name}-{unique_suffix}"
            self.key_pair_resource = ec2.CfnKeyPair(self, "DebugKeyPair", key_name=generated_key_name)
            key_name = generated_key_name

        # SG para ALB
        self.alb_sg = ec2.SecurityGroup(self, "SwaggerALBSG",
            vpc=self.vpc,
            description="ALB SG",
            allow_all_outbound=True
        )
        if expose_swagger_public:
            if restrict_swagger_to_cidr:
                for port in (80, 443):
                    self.alb_sg.add_ingress_rule(ec2.Peer.ipv4(restrict_swagger_to_cidr), ec2.Port.tcp(port), f"HTTP(S) restricted {port}")
            else:
                for port in (80, 443):
                    self.alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(port), f"HTTP(S) {port}")
        # Permitir tráfego do ALB para serviços
        for p in (8000, 3000):
            self.internal_sg.add_ingress_rule(self.alb_sg, ec2.Port.tcp(p), f"ALB to {p}")

        # IAM Role
        self.ec2_role = iam.Role(self, "EC2Role",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore")]
        )
        self.config_bucket.grant_read(self.ec2_role)
        self.ec2_role.add_to_policy(iam.PolicyStatement(
            actions=["ssm:GetParameter","ssm:GetParameters","ssm:GetParametersByPath","secretsmanager:GetSecretValue","secretsmanager:DescribeSecret"],
            resources=[
                f"arn:aws:ssm:{self.region}:{self.account}:parameter/app/*",
                f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:app/*"
            ]
        ))

        cpu_type = ec2.AmazonLinuxCpuType.ARM_64 if arch == "ARM_64" else ec2.AmazonLinuxCpuType.X86_64
        self.ami = ec2.AmazonLinuxImage(generation=ec2.AmazonLinuxGeneration.AMAZON_LINUX_2023, cpu_type=cpu_type)

        # UserData FastAPI
        self.fastapi_user_data = ec2.UserData.for_linux()
        self.fastapi_user_data.add_commands(
            "set -eux",
            "exec > /var/log/fastapi-user-data.log 2>&1",
            "yum update -y || true",
            "curl -fsSL https://get.docker.com | sh",
            "systemctl enable docker && systemctl start docker",
            "usermod -aG docker ec2-user",
            f"aws s3 cp s3://{self.config_bucket.bucket_name}/fastapi/docker-compose.yml /opt/app/docker-compose.yml || true",
            f"aws s3 cp s3://{self.config_bucket.bucket_name}/fastapi/.env /opt/app/.env || true",
            "mkdir -p /opt/app",
            "[ -f /opt/app/docker-compose.yml ] || cat > /opt/app/docker-compose.yml <<'EOF'\nversion: '3.8'\nservices:\n  fastapi:\n    image: python:3.11-slim\n    ports:\n      - '8000:8000'\n    command: sh -c 'pip install fastapi uvicorn && uvicorn main:app --host 0.0.0.0 --port 8000'\n    restart: always\nEOF",
            "[ -f /opt/app/main.py ] || cat > /opt/app/main.py <<'PY'\nfrom fastapi import FastAPI;app=FastAPI(title='FastAPI');@app.get('/health')\ndef h():return {'status':'ok'}\nPY",
            "curl -L https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m) -o /usr/local/bin/docker-compose && chmod +x /usr/local/bin/docker-compose && ln -sf /usr/local/bin/docker-compose /usr/bin/docker-compose || true",
            "cd /opt/app && docker-compose up -d"
        )

        # UserData Gateway
        self.gateway_user_data = ec2.UserData.for_linux()
        self.gateway_user_data.add_commands(
            "set -eux",
            "exec > /var/log/gateway-user-data.log 2>&1",
            "yum update -y || true",
            "curl -fsSL https://get.docker.com | sh",
            "systemctl enable docker && systemctl start docker",
            "usermod -aG docker ec2-user",
            f"aws s3 cp s3://{self.config_bucket.bucket_name}/gateway/docker-compose.yml /opt/gateway/docker-compose.yml || true",
            f"aws s3 cp s3://{self.config_bucket.bucket_name}/gateway/.env /opt/gateway/.env || true",
            "mkdir -p /opt/gateway",
            "[ -f /opt/gateway/docker-compose.yml ] || cat > /opt/gateway/docker-compose.yml <<'EOF'\nversion: '3.8'\nservices:\n  gateway:\n    image: node:18-alpine\n    ports:\n      - '3000:3000'\n    command: sh -c 'npm install express swagger-ui-express && node server.js'\n    restart: always\nEOF",
            "[ -f /opt/gateway/server.js ] || cat > /opt/gateway/server.js <<'JS'\nconst express=require('express');const swaggerUi=require('swagger-ui-express');const app=express();const doc={openapi:'3.0.0',info:{title:'Gateway',version:'1.0.0'},paths:{'/health':{get:{responses:{'200':{description:'OK'}}}}}};app.get('/health',(r,s)=>s.json({status:'ok'}));app.use('/api-docs',swaggerUi.serve,swaggerUi.setup(doc));app.listen(3000,'0.0.0.0',()=>console.log('gateway up'));\nJS",
            "curl -L https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m) -o /usr/local/bin/docker-compose && chmod +x /usr/local/bin/docker-compose && ln -sf /usr/local/bin/docker-compose /usr/bin/docker-compose || true",
            "cd /opt/gateway && docker-compose up -d"
        )

        # Instâncias fixas (sem ASG)
        self.fastapi_instance = ec2.Instance(self, "FastAPIInstance",
            vpc=self.vpc,
            instance_type=ec2.InstanceType("t4g.micro"),
            machine_image=self.ami,
            security_group=self.internal_sg,
            user_data=self.fastapi_user_data,
            role=self.ec2_role,
            key_pair=ec2.KeyPair.from_key_pair_name(self, "FastAPIKeyPair", key_name),
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC)
        )
        self.gateway_instance = ec2.Instance(self, "GatewayInstance",
            vpc=self.vpc,
            instance_type=ec2.InstanceType("t4g.medium"),
            machine_image=self.ami,
            security_group=self.internal_sg,
            user_data=self.gateway_user_data,
            role=self.ec2_role,
            key_pair=ec2.KeyPair.from_key_pair_name(self, "GatewayKeyPair", key_name),
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC)
        )

        # ALB + TargetGroups
        self.swagger_alb = elbv2.ApplicationLoadBalancer(self, "SwaggerALB",
            vpc=self.vpc,
            internet_facing=expose_swagger_public,
            security_group=self.alb_sg
        )
        self.fastapi_tg = elbv2.ApplicationTargetGroup(self, "FastAPITG",
            vpc=self.vpc, port=8000, protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.INSTANCE,
            health_check=elbv2.HealthCheck(path="/docs", healthy_http_codes="200")
        )
        self.gateway_tg = elbv2.ApplicationTargetGroup(self, "GatewayTG",
            vpc=self.vpc, port=3000, protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.INSTANCE,
            health_check=elbv2.HealthCheck(path="/api-docs", healthy_http_codes="200")
        )
        # Adicionar instâncias como targets
        self.fastapi_tg.add_target(elbv2.InstanceTarget(self.fastapi_instance.instance_id, port=8000))
        self.gateway_tg.add_target(elbv2.InstanceTarget(self.gateway_instance.instance_id, port=3000))
        self.alb_listener = self.swagger_alb.add_listener("HttpListener", port=80, open=expose_swagger_public,
            default_action=elbv2.ListenerAction.fixed_response(404, content_type="text/plain", message_body="Not Found"))
        self.alb_listener.add_action("FastAPIRule", priority=10,
            conditions=[elbv2.ListenerCondition.path_patterns(["/swagger/api/*"])],
            action=elbv2.ListenerAction.forward([self.fastapi_tg]))
        self.alb_listener.add_action("GatewayRule", priority=20,
            conditions=[elbv2.ListenerCondition.path_patterns(["/swagger/gw/*"])],
            action=elbv2.ListenerAction.forward([self.gateway_tg]))

        # Outputs principais (dentro do __init__)
        CfnOutput(self, "PersistentMode", value="False", description="Modo duas instâncias fixas (sem ASG)")
        CfnOutput(self, "VpcId", value=self.vpc.vpc_id)
        CfnOutput(self, "InternalSGId", value=self.internal_sg.security_group_id)
        CfnOutput(self, "ConfigBucketName", value=self.config_bucket.bucket_name)
        CfnOutput(self, "SSHKeyName", value=key_name)
        CfnOutput(self, "FastAPIInstanceId", value=self.fastapi_instance.instance_id)
        CfnOutput(self, "GatewayInstanceId", value=self.gateway_instance.instance_id)
        CfnOutput(self, "SwaggerAlbDnsName", value=self.swagger_alb.load_balancer_dns_name)
        CfnOutput(self, "FastAPISwaggerUrl", value=f"http://{self.swagger_alb.load_balancer_dns_name}/swagger/api/docs")
        CfnOutput(self, "GatewaySwaggerUrl", value=f"http://{self.swagger_alb.load_balancer_dns_name}/swagger/gw/api-docs")

        # SSM Parameters essenciais (duas instâncias)
        ssm.StringParameter(self, "ParamConfigBucket", parameter_name="/infra/cdk/config/bucket", string_value=self.config_bucket.bucket_name)
        ssm.StringParameter(self, "ParamSshKeyName", parameter_name="/infra/cdk/ssh/key-name", string_value=key_name)
        ssm.StringParameter(self, "ParamInternalSg", parameter_name="/infra/cdk/security/internal-sg-id", string_value=self.internal_sg.security_group_id)
        ssm.StringParameter(self, "ParamPersistent", parameter_name="/infra/cdk/mode/persistent", string_value="false")
        ssm.StringParameter(self, "ParamFastapiTg", parameter_name="/infra/cdk/fastapi/target-group-arn", string_value=self.fastapi_tg.target_group_arn)
        ssm.StringParameter(self, "ParamGatewayTg", parameter_name="/infra/cdk/gateway/target-group-arn", string_value=self.gateway_tg.target_group_arn)
        ssm.StringParameter(self, "ParamAlbDns", parameter_name="/infra/cdk/alb/dns", string_value=self.swagger_alb.load_balancer_dns_name)
        ssm.StringParameter(self, "ParamFastapiInstanceId", parameter_name="/infra/cdk/fastapi/instance-id", string_value=self.fastapi_instance.instance_id)
        ssm.StringParameter(self, "ParamGatewayInstanceId", parameter_name="/infra/cdk/gateway/instance-id", string_value=self.gateway_instance.instance_id)