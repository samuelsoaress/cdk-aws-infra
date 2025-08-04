from aws_cdk import (
    Stack,
    CfnOutput,
    aws_ec2 as ec2,
    aws_ssm as ssm,
)
from constructs import Construct

class InfrastructureStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # VPC
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

        # Security Group para FastAPI
        fastapi_sg = ec2.SecurityGroup(self, "FastAPISG",
            vpc=self.vpc,
            description="Security group for FastAPI instance",
            allow_all_outbound=True
        )
        fastapi_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(22), "SSH")
        fastapi_sg.add_ingress_rule(ec2.Peer.ipv4(self.vpc.vpc_cidr_block), ec2.Port.tcp(8000), "FastAPI")

        # Security Group para Gateway
        gateway_sg = ec2.SecurityGroup(self, "GatewaySG",
            vpc=self.vpc,
            description="Security group for Gateway instance",
            allow_all_outbound=True
        )
        gateway_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(22), "SSH")
        gateway_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "HTTP")
        gateway_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "HTTPS")

        # Criar Key Pairs automaticamente
        fastapi_key_pair = ec2.KeyPair(self, "FastAPIKeyPair",
            key_pair_name="chave-fastapi",
            type=ec2.KeyPairType.RSA
        )
        
        gateway_key_pair = ec2.KeyPair(self, "GatewayKeyPair",
            key_pair_name="chave-gateway",
            type=ec2.KeyPairType.RSA
        )

        # Armazenar chaves privadas no SSM Parameter Store
        ssm.StringParameter(self, "FastAPIPrivateKey",
            parameter_name="/ec2/keypair/chave-fastapi/private",
            string_value=fastapi_key_pair.private_key.to_string(),
            description="FastAPI instance private key"
        )

        ssm.StringParameter(self, "GatewayPrivateKey",
            parameter_name="/ec2/keypair/chave-gateway/private",
            string_value=gateway_key_pair.private_key.to_string(),
            description="Gateway instance private key"
        )

        # FastAPI EC2 Instance
        self.fastapi_instance = ec2.Instance(self, "FastAPIInstance",
            instance_type=ec2.InstanceType("t4g.micro"),
            machine_image=ec2.AmazonLinuxImage(
                generation=ec2.AmazonLinuxGeneration.AMAZON_LINUX_2,
                cpu_type=ec2.AmazonLinuxCpuType.ARM_64
            ),
            vpc=self.vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_group=fastapi_sg,
            key_pair=fastapi_key_pair,
        )

        # Gateway EC2 Instance
        self.gateway_instance = ec2.Instance(self, "GatewayInstance",
            instance_type=ec2.InstanceType("t4g.medium"),
            machine_image=ec2.AmazonLinuxImage(
                generation=ec2.AmazonLinuxGeneration.AMAZON_LINUX_2,
                cpu_type=ec2.AmazonLinuxCpuType.ARM_64
            ),
            vpc=self.vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_group=gateway_sg,
            key_pair=gateway_key_pair,
        )

        # Outputs
        CfnOutput(self, "VpcId",
            value=self.vpc.vpc_id,
            description="VPC ID"
        )

        CfnOutput(self, "FastAPIPublicIP",
            value=self.fastapi_instance.instance_public_ip,
            description="FastAPI instance public IP"
        )

        CfnOutput(self, "GatewayPublicIP",
            value=self.gateway_instance.instance_public_ip,
            description="Gateway instance public IP"
        )

        # Outputs das Key Pairs
        CfnOutput(self, "FastAPIKeyPairId",
            value=fastapi_key_pair.key_pair_id,
            description="FastAPI Key Pair ID"
        )

        CfnOutput(self, "GatewayKeyPairId",
            value=gateway_key_pair.key_pair_id,
            description="Gateway Key Pair ID"
        )

        # Outputs para acessar chaves privadas
        CfnOutput(self, "FastAPIPrivateKeySSMPath",
            value="/ec2/keypair/chave-fastapi/private",
            description="SSM Parameter path for FastAPI private key"
        )

        CfnOutput(self, "GatewayPrivateKeySSMPath",
            value="/ec2/keypair/chave-gateway/private",
            description="SSM Parameter path for Gateway private key"
        )