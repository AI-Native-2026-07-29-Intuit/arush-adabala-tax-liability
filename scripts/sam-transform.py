"""Run the official AWS SAM transform (samtranslator) offline against template.yaml.

Usage:  python3 scripts/sam-transform.py template.yaml
Needs:  pip install aws-sam-translator   (and AWS_DEFAULT_REGION set; no credentials)


This is the same library CloudFormation runs server-side for
Transform: AWS::Serverless-2016-10-31, so its output is the authoritative answer to
"what does my template actually expand into" - independent of any emulator's
reimplementation, and needing no AWS account.
"""
import json
import sys

from samtranslator.parser.parser import Parser
from samtranslator.translator.translator import Translator
from samtranslator.yaml_helper import yaml_parse


class OfflineManagedPolicyLoader:
    """Stands in for the IAM-backed loader.

    The real one calls iam:ListPolicies to map friendly names to ARNs. Only the
    AWS-managed names SAM itself injects are needed here, and their ARNs are stable
    and public, so no credentials are required.
    """

    def load(self):
        return {
            "AWSLambdaBasicExecutionRole":
                "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            "AWSLambdaRole":
                "arn:aws:iam::aws:policy/service-role/AWSLambdaRole",
            "AWSXrayWriteOnlyAccess":
                "arn:aws:iam::aws:policy/AWSXrayWriteOnlyAccess",
            "AWSLambdaVPCAccessExecutionRole":
                "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
        }


def main(path):
    with open(path) as fh:
        template = yaml_parse(fh)

    # `sam deploy` packages first: it uploads the built artifact and rewrites CodeUri to the
    # resulting s3:// URI before CloudFormation ever sees the template. The transform enforces
    # that, so do the same substitution here - it is what the service actually receives.
    for res in template.get("Resources", {}).values():
        if res.get("Type") == "AWS::Serverless::Function":
            res["Properties"]["CodeUri"] = "s3://taxcalc-sam-artifacts/packaged-artifact-placeholder"

    translator = Translator(
        managed_policy_map=OfflineManagedPolicyLoader().load(),
        sam_parser=Parser(),
    )
    transformed = translator.translate(
        sam_template=template,
        parameter_values={"StageName": "dev", "LogRetentionDays": "14"},
    )
    print(json.dumps(transformed, indent=2))


if __name__ == "__main__":
    main(sys.argv[1])
