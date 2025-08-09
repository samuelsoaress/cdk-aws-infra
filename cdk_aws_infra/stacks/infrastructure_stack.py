from aws_cdk import (
    Stack,
    CfnOutput,
    RemovalPolicy,
    aws_ec2 as ec2,
    aws_ssm as ssm,
    aws_iam as iam,
    aws_s3 as s3,
)
from constructs import Construct


class InfrastructureStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # Context / flags
        # MODO FIXO: sempre instância única (persistent). Ignora contextos anteriores de ASG/ALB.
        arch = self.node.try_get_context("arch") or "ARM_64"
        persistent_mode = True  # fixado
        ssh_key_name = self.node.try_get_context("ssh_key_name") or "cdk-aws-infra-debug-key"
        reuse_ssh_key = bool(self.node.try_get_context("reuse_ssh_key"))

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

        # (Removido ALB/ASG path) – somente portas diretas já configuradas acima.

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

        # Instância dev única (modo fixo)
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

        # Outputs dev únicos
        CfnOutput(self, "PersistentMode", value="True", description="Modo instância única fixo")
        CfnOutput(self, "VpcId", value=self.vpc.vpc_id)
        CfnOutput(self, "InternalSGId", value=self.internal_sg.security_group_id)
        CfnOutput(self, "ConfigBucketName", value=self.config_bucket.bucket_name)
        CfnOutput(self, "SSHKeyName", value=key_name)
        CfnOutput(self, "DevInstanceId", value=self.dev_instance.instance_id)
        CfnOutput(self, "DevInstancePublicIp", value=self.dev_instance.instance_public_ip)
        CfnOutput(self, "DevFastApiUrl", value=f"http://{self.dev_instance.instance_public_ip}:8000/health")
        CfnOutput(self, "DevGatewayUrl", value=f"http://{self.dev_instance.instance_public_ip}:3000/api-docs")

        # SSM Parameters essenciais (modo único)
        ssm.StringParameter(self, "ParamConfigBucket", parameter_name="/infra/cdk/config/bucket", string_value=self.config_bucket.bucket_name)
        ssm.StringParameter(self, "ParamSshKeyName", parameter_name="/infra/cdk/ssh/key-name", string_value=key_name)
        ssm.StringParameter(self, "ParamInternalSg", parameter_name="/infra/cdk/security/internal-sg-id", string_value=self.internal_sg.security_group_id)
        ssm.StringParameter(self, "ParamPersistent", parameter_name="/infra/cdk/mode/persistent", string_value="true")
        ssm.StringParameter(self, "ParamDevInstanceId", parameter_name="/infra/cdk/dev/instance-id", string_value=self.dev_instance.instance_id)
        ssm.StringParameter(self, "ParamDevInstancePublicIp", parameter_name="/infra/cdk/dev/public-ip", string_value=self.dev_instance.instance_public_ip)