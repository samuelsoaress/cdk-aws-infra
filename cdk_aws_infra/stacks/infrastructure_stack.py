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
)
from constructs import Construct


class InfrastructureStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # Context / flags
        arch = self.node.try_get_context("arch") or "ARM_64"
        expose_swagger_public = self.node.try_get_context("expose_swagger_public")
        if expose_swagger_public is None:
            expose_swagger_public = True
        restrict_swagger_to_cidr = self.node.try_get_context("restrict_swagger_to_cidr")
        use_eip = self.node.try_get_context("use_eip") or False
        persistent_mode = self.node.try_get_context("persistent_mode") or False
        ssh_key_name = self.node.try_get_context("ssh_key_name") or "cdk-aws-infra-debug-key"
        reuse_ssh_key = bool(self.node.try_get_context("reuse_ssh_key"))  # -c reuse_ssh_key=true para não criar CfnKeyPair

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

        # KeyPair (customizável). Se reuse_ssh_key=true não cria recurso novo (evita erro de já existente)
        if reuse_ssh_key:
            key_name = ssh_key_name
            self.key_pair_resource = None
        else:
            self.key_pair_resource = ec2.CfnKeyPair(self, "DebugKeyPair", key_name=ssh_key_name)
            key_name = ssh_key_name

        # SG para ALB (pode ficar ocioso em persistent mode)
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

        # Permitir tráfego do ALB para serviços (não prejudica se ALB não for criado)
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

        # UserData base (mantém original para ASG)
        self.fastapi_user_data = ec2.UserData.for_linux()
        self.fastapi_user_data.add_commands(
            "set -eux",
            "exec > /var/log/user-data.log 2>&1",
            "yum update -y || true",
            "curl -fsSL https://get.docker.com | sh",
            "systemctl enable docker && systemctl start docker",
            "usermod -aG docker ec2-user",
            f"aws s3 cp s3://{self.config_bucket.bucket_name}/fastapi/docker-compose.yml /opt/app/docker-compose.yml || true",
            f"aws s3 cp s3://{self.config_bucket.bucket_name}/fastapi/.env /opt/app/.env || true",
            "mkdir -p /opt/app",
            "[ -f /opt/app/docker-compose.yml ] || cat > /opt/app/docker-compose.yml <<'EOF'\nversion: '3.8'\nservices:\n  fastapi:\n    image: python:3.11-slim\n    ports:\n      - '8000:8000'\n    command: sh -c 'pip install fastapi uvicorn && uvicorn main:app --host 0.0.0.0 --port 8000'\n    restart: always\nEOF",
            "[ -f /opt/app/main.py ] || cat > /opt/app/main.py <<'PY'\nfrom fastapi import FastAPI;app=FastAPI(title='FastAPI Demo');@app.get('/health')\ndef h():return {'status':'ok'}\nPY",
            "curl -L https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m) -o /usr/local/bin/docker-compose && chmod +x /usr/local/bin/docker-compose && ln -sf /usr/local/bin/docker-compose /usr/bin/docker-compose || true",
            "cd /opt/app && docker-compose up -d"
        )

        self.gateway_user_data = ec2.UserData.for_linux()
        self.gateway_user_data.add_commands(
            "set -eux",
            "exec > /var/log/user-data.log 2>&1",
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

        if not persistent_mode:
            # LaunchTemplates + ASGs
            self.fastapi_lt = ec2.LaunchTemplate(self, "FastAPILT",
                machine_image=self.ami,
                instance_type=ec2.InstanceType("t4g.micro"),
                security_group=self.internal_sg,
                user_data=self.fastapi_user_data,
                role=self.ec2_role,
                key_pair=ec2.KeyPair.from_key_pair_name(self, "FastAPIKeyPair", key_name)
            )
            self.gateway_lt = ec2.LaunchTemplate(self, "GatewayLT",
                machine_image=self.ami,
                instance_type=ec2.InstanceType("t4g.medium"),
                security_group=self.internal_sg,
                user_data=self.gateway_user_data,
                role=self.ec2_role,
                key_pair=ec2.KeyPair.from_key_pair_name(self, "GatewayKeyPair", key_name)
            )
            self.fastapi_asg = autoscaling.AutoScalingGroup(self, "FastAPIASG",
                vpc=self.vpc,
                launch_template=self.fastapi_lt,
                min_capacity=1, max_capacity=1, desired_capacity=1,
                vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC)
            )
            self.gateway_asg = autoscaling.AutoScalingGroup(self, "GatewayASG",
                vpc=self.vpc,
                launch_template=self.gateway_lt,
                min_capacity=1, max_capacity=1, desired_capacity=1,
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
            self.fastapi_asg.attach_to_application_target_group(self.fastapi_tg)
            self.gateway_asg.attach_to_application_target_group(self.gateway_tg)
            self.alb_listener = self.swagger_alb.add_listener("HttpListener", port=80, open=expose_swagger_public,
                default_action=elbv2.ListenerAction.fixed_response(404, content_type="text/plain", message_body="Not Found"))
            self.alb_listener.add_action("FastAPIRule", priority=10,
                conditions=[elbv2.ListenerCondition.path_patterns(["/swagger/api/*"])],
                action=elbv2.ListenerAction.forward([self.fastapi_tg]))
            self.alb_listener.add_action("GatewayRule", priority=20,
                conditions=[elbv2.ListenerCondition.path_patterns(["/swagger/gw/*"])],
                action=elbv2.ListenerAction.forward([self.gateway_tg]))

            if use_eip:
                self.fastapi_eip = ec2.CfnEIP(self, "FastAPIEIP", domain="vpc")
                self.gateway_eip = ec2.CfnEIP(self, "GatewayEIP", domain="vpc")
        else:
            # Instância única dev
            dev_user_data = ec2.UserData.for_linux()
            dev_user_data.add_commands(
                "set -eux",
                "exec > /var/log/user-data.log 2>&1",
                "yum update -y || true",
                "curl -fsSL https://get.docker.com | sh",
                "systemctl enable docker && systemctl start docker",
                "usermod -aG docker ec2-user",
                "curl -L https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m) -o /usr/local/bin/docker-compose && chmod +x /usr/local/bin/docker-compose && ln -sf /usr/local/bin/docker-compose /usr/bin/docker-compose || true",
                "mkdir -p /opt/dev /code /app",
                "cat > /opt/dev/docker-compose.yml <<'EOF'\nversion: '3.8'\nservices:\n  fastapi:\n    image: python:3.11-slim\n    ports:\n      - '8000:8000'\n    command: sh -c 'pip install fastapi uvicorn && uvicorn main:app --host 0.0.0.0 --port 8000'\n    volumes:\n      - /code:/code\n    restart: always\n  gateway:\n    image: node:18-alpine\n    ports:\n      - '3000:3000'\n    command: sh -c 'npm install express swagger-ui-express && node server.js'\n    volumes:\n      - /app:/app\n    restart: always\nEOF",
                "[ -f /code/main.py ] || cat > /code/main.py <<'PY'\nfrom fastapi import FastAPI;app=FastAPI(title='FastAPI Dev');@app.get('/health')\ndef h():return {'status':'ok'}\nPY",
                "[ -f /app/server.js ] || cat > /app/server.js <<'JS'\nconst express=require('express');const swaggerUi=require('swagger-ui-express');const app=express();const doc={openapi:'3.0.0',info:{title:'Gateway Dev',version:'1.0.0'},paths:{'/health':{get:{responses:{'200':{description:'OK'}}}}}};app.get('/health',(r,s)=>s.json({status:'ok'}));app.use('/api-docs',swaggerUi.serve,swaggerUi.setup(doc));app.listen(3000,'0.0.0.0',()=>console.log('gateway dev up'));\nJS",
                "cd /opt/dev && docker-compose up -d",
                "echo 'DEV READY'"
            )
            for p in (8000, 3000):
                self.internal_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(p), f"Dev direct {p}")
            self.dev_instance = ec2.Instance(self, "DevInstance",
                vpc=self.vpc,
                instance_type=ec2.InstanceType("t4g.medium"),
                machine_image=self.ami,
                security_group=self.internal_sg,
                user_data=dev_user_data,
                role=self.ec2_role,
                key_pair=ec2.KeyPair.from_key_pair_name(self, "DevKeyPair", key_name),
                vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC)
            )

        # Outputs comuns
        CfnOutput(self, "PersistentMode", value=str(persistent_mode), description="Modo instância única")
        CfnOutput(self, "VpcId", value=self.vpc.vpc_id)
        CfnOutput(self, "InternalSGId", value=self.internal_sg.security_group_id)
        CfnOutput(self, "ConfigBucketName", value=self.config_bucket.bucket_name)
        CfnOutput(self, "SSHKeyName", value=key_name)

        if not persistent_mode:
            CfnOutput(self, "SwaggerAlbDnsName", value=self.swagger_alb.load_balancer_dns_name)
            CfnOutput(self, "FastAPISwaggerUrl", value=f"http://{self.swagger_alb.load_balancer_dns_name}/swagger/api/docs")
            CfnOutput(self, "GatewaySwaggerUrl", value=f"http://{self.swagger_alb.load_balancer_dns_name}/swagger/gw/api-docs")
            if use_eip:
                CfnOutput(self, "FastAPIEIPAllocationId", value=self.fastapi_eip.attr_allocation_id)
                CfnOutput(self, "GatewayEIPAllocationId", value=self.gateway_eip.attr_allocation_id)
        else:
            CfnOutput(self, "DevInstanceId", value=self.dev_instance.instance_id)
            CfnOutput(self, "DevInstancePublicIp", value=self.dev_instance.instance_public_ip)
            CfnOutput(self, "DevFastApiUrl", value=f"http://{self.dev_instance.instance_public_ip}:8000/health")
            CfnOutput(self, "DevGatewayUrl", value=f"http://{self.dev_instance.instance_public_ip}:3000/api-docs")

        # SSM Parameters
        ssm.StringParameter(self, "ParamConfigBucket", parameter_name="/infra/cdk/config/bucket", string_value=self.config_bucket.bucket_name)
        ssm.StringParameter(self, "ParamSshKeyName", parameter_name="/infra/cdk/ssh/key-name", string_value=key_name)
        ssm.StringParameter(self, "ParamInternalSg", parameter_name="/infra/cdk/security/internal-sg-id", string_value=self.internal_sg.security_group_id)
        ssm.StringParameter(self, "ParamPersistent", parameter_name="/infra/cdk/mode/persistent", string_value=str(persistent_mode).lower())
        if not persistent_mode:
            ssm.StringParameter(self, "ParamAlbDns", parameter_name="/infra/cdk/alb/dns", string_value=self.swagger_alb.load_balancer_dns_name)
            ssm.StringParameter(self, "ParamFastapiAsg", parameter_name="/infra/cdk/fastapi/asg-name", string_value=self.fastapi_asg.auto_scaling_group_name)
            ssm.StringParameter(self, "ParamGatewayAsg", parameter_name="/infra/cdk/gateway/asg-name", string_value=self.gateway_asg.auto_scaling_group_name)
            ssm.StringParameter(self, "ParamFastapiTg", parameter_name="/infra/cdk/fastapi/target-group-arn", string_value=self.fastapi_tg.target_group_arn)
            ssm.StringParameter(self, "ParamGatewayTg", parameter_name="/infra/cdk/gateway/target-group-arn", string_value=self.gateway_tg.target_group_arn)
            ssm.StringParameter(self, "ParamAlbSg", parameter_name="/infra/cdk/security/alb-sg-id", string_value=self.alb_sg.security_group_id)
            if use_eip:
                ssm.StringParameter(self, "ParamFastapiEip", parameter_name="/infra/cdk/eip/fastapi", string_value=self.fastapi_eip.attr_allocation_id)
                ssm.StringParameter(self, "ParamGatewayEip", parameter_name="/infra/cdk/eip/gateway", string_value=self.gateway_eip.attr_allocation_id)
        else:
            # Parâmetros específicos de dev (instância única)
            ssm.StringParameter(self, "ParamDevInstanceId", parameter_name="/infra/cdk/dev/instance-id", string_value=self.dev_instance.instance_id)
            ssm.StringParameter(self, "ParamDevInstancePublicIp", parameter_name="/infra/cdk/dev/public-ip", string_value=self.dev_instance.instance_public_ip)