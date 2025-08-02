#!/usr/bin/env python3
import os
import aws_cdk as cdk
from cdk_aws_infra.stacks.frontend_stack import FrontendStack
from cdk_aws_infra.stacks.infrastructure_stack import InfrastructureStack

app = cdk.App()

# Environment
env = cdk.Environment(
    account=os.getenv('CDK_DEFAULT_ACCOUNT'),
    region=os.getenv('CDK_DEFAULT_REGION') or 'us-east-1'
)

# Stacks
frontend_stack = FrontendStack(app, "FrontendStack", env=env)
infrastructure_stack = InfrastructureStack(app, "InfrastructureStack", env=env)

app.synth()
