from aws_cdk import (
    Stack,
    RemovalPolicy,
    CfnOutput,
    aws_s3 as s3,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_iam as iam,
)
from constructs import Construct

class FrontendStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # Bucket S3 para arquivos do React
        self.site_bucket = s3.Bucket(self, "FrontendBucket",
            website_index_document="index.html",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            public_read_access=False,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True
        )

        # Distribuição CloudFront (usando S3BucketOrigin em vez de S3Origin)
        self.distribution = cloudfront.Distribution(self, "FrontendDistribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin(self.site_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            ),
            default_root_object="index.html"
        )

        # Permissões para CI/CD
        self.site_bucket.grant_read_write(iam.AccountPrincipal(self.account))

        # Outputs para CI/CD
        CfnOutput(self, "BucketName",
            value=self.site_bucket.bucket_name,
            description="S3 Bucket name for frontend"
        )

        CfnOutput(self, "CloudFrontDomain",
            value=self.distribution.domain_name,
            description="CloudFront distribution domain"
        )

        CfnOutput(self, "CloudFrontDistributionId",
            value=self.distribution.distribution_id,
            description="CloudFront distribution ID for invalidation"
        )