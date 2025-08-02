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

        # Origin Access Control (OAC) usando CFN
        oac = cloudfront.CfnOriginAccessControl(self, "OAC",
            origin_access_control_config=cloudfront.CfnOriginAccessControl.OriginAccessControlConfigProperty(
                name="S3OAC",
                origin_access_control_origin_type="s3",
                signing_behavior="always",
                signing_protocol="sigv4"
            )
        )

        # Distribuição CloudFront
        self.distribution = cloudfront.Distribution(self, "FrontendDistribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin(self.site_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            ),
            default_root_object="index.html"
        )

        # Associar OAC à origem S3 usando escape hatch
        cfn_distribution = self.distribution.node.default_child
        cfn_distribution.add_property_override(
            "DistributionConfig.Origins.0.OriginAccessControlId", oac.attr_id
        )
        cfn_distribution.add_property_override(
            "DistributionConfig.Origins.0.S3OriginConfig.OriginAccessIdentity", ""
        )

        # Política do bucket S3 para permitir acesso do CloudFront via OAC
        self.site_bucket.add_to_resource_policy(iam.PolicyStatement(
            actions=["s3:GetObject"],
            resources=[self.site_bucket.arn_for_objects("*")],
            principals=[iam.ServicePrincipal("cloudfront.amazonaws.com")],
            conditions={
                "StringEquals": {
                    "AWS:SourceArn": f"arn:aws:cloudfront::{self.account}:distribution/{self.distribution.distribution_id}"
                }
            }
        ))

        # Permissões para CI/CD (deploy de arquivos)
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

        CfnOutput(self, "CloudFrontURL",
            value=f"https://{self.distribution.domain_name}",
            description="Complete CloudFront URL"
        )