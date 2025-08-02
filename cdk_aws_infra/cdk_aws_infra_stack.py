from aws_cdk import Stack
from constructs import Construct

class CdkAwsInfraStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Esta stack pode ser usada como orquestrador central
        # se você precisar coordenar recursos entre outras stacks
        pass
