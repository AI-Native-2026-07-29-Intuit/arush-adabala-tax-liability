#!/usr/bin/env python3
"""Decide whether AWS would deny the agent's LLM spend once the BudgetAction has fired.

**This is NOT a mock of AWS, and an emulator is the wrong tool for this question.** Mocking the
account-side resources is worthless here for the same reason it was worthless for the W6 D1 OIDC
trust policy (see ``scripts/oidc-trust-simulate.py``): emulators do not evaluate policy, they
return success. Measured against floci 2.0.1 on 2026-09-20:

* ``aws budgets describe-budgets`` and ``describe-budget-actions-for-budget`` ->
  ``UnknownOperationException``. There is no Budgets service to ask.
* ``aws cloudformation deploy`` of this stack -> ``CREATE_COMPLETE`` - and so does a template
  carrying four property names that real CloudFormation rejects outright.
* ``aws iam simulate-custom-policy`` -> ``UnsupportedOperation``. There is no evaluator either.

So a green emulator run would print exactly the reassuring output an on-call wants to see while
proving nothing whatever. This script instead reproduces the decision procedure, which for this
question is small, fully documented by AWS, and deterministic:

    An explicit Deny in any applicable policy overrides every Allow, anywhere.

That single rule is what makes a BudgetAction a cap rather than a suggestion, and it is the only
rule this file needs, because the attached policy is Deny-only.

**What this DOES answer**: given the policy the template now creates, would the actions the cap
is supposed to stop be denied - and, just as important, would unrelated actions still be
permitted, so the cap is not a blanket outage dressed up as cost control.

**What it does NOT answer**: whether AWS accepts the stack, whether the Budgets service fires the
action at 100%, and whether the account's own SCPs or permission boundaries change the outcome.
Those need a real account; they are listed by ``verify-budget-stack.sh`` rather than implied.
"""

from __future__ import annotations

import pathlib
import sys
from typing import Any

#: The template whose policy is evaluated. Read from the committed YAML rather than duplicated
#: here: a simulator with its own copy of the policy tests the copy, not the artefact that ships.
TEMPLATE = pathlib.Path(__file__).resolve().parents[1] / "cfn" / "agent-svc-budget.yaml"


def load_policy_statements(template_path: pathlib.Path) -> list[dict[str, Any]]:
    """Extract the DENY policy's statements from the CloudFormation template.

    Parsed with a deliberately small YAML reader rather than a full CloudFormation parser: the
    only intrinsic in the policy body is ``!Ref``, and resolving it to its parameter default is
    exactly what a deploy with no overrides would do.

    :param template_path: The template to read.
    :returns: The policy statements, with ``!Ref`` resolved to parameter defaults.
    :raises SystemExit: if the policy resource is missing, which would mean the cap has gone back
        to pointing at a policy nobody wrote.
    """
    import yaml

    class Loader(yaml.SafeLoader):
        """A loader that keeps CloudFormation's short-form intrinsics as data."""

    def ref(loader: yaml.Loader, node: yaml.Node) -> dict[str, Any]:
        """Represent ``!Ref X`` as ``{"Ref": "X"}``.

        :param loader: The YAML loader.
        :param node: The node being constructed.
        :returns: The intrinsic, as a mapping.
        """
        return {"Ref": str(loader.construct_scalar(node))}  # type: ignore[arg-type]

    def passthrough(loader: yaml.Loader, node: yaml.Node) -> Any:
        """Keep every other intrinsic as a plain value.

        :param loader: The YAML loader.
        :param node: The node being constructed.
        :returns: The node's scalar or sequence value.
        """
        if isinstance(node, yaml.ScalarNode):
            return loader.construct_scalar(node)
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node)
        return loader.construct_mapping(node)

    Loader.add_constructor("!Ref", ref)
    for tag in ("!Sub", "!GetAtt", "!Join", "!Select", "!Equals", "!If", "!ImportValue"):
        Loader.add_constructor(tag, passthrough)


    # CloudFormation's short-form intrinsics. ruff matches on the argument's NAME, not on its
    # base class, so the rule fires on a loader that is in fact safe.
    doc = yaml.load(template_path.read_text(), Loader=Loader)  # noqa: S506
    params = {k: v.get("Default") for k, v in (doc.get("Parameters") or {}).items()}
    resource = (doc.get("Resources") or {}).get("DenyLlmSpendPolicy")
    if resource is None:
        sys.exit(
            "FAIL: DenyLlmSpendPolicy is not in the template. The BudgetAction would be "
            "attaching a policy that exists nowhere in this repository - which is the exact "
            "defect this script was written to close."
        )

    def resolve(value: Any) -> Any:
        """Replace ``{"Ref": p}`` with parameter ``p``'s default.

        :param value: A node from the policy document.
        :returns: The node with refs resolved.
        """
        if isinstance(value, dict):
            if set(value) == {"Ref"}:
                return params.get(value["Ref"], value["Ref"])
            return {k: resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [resolve(v) for v in value]
        return value

    statements = resolve(resource["Properties"]["PolicyDocument"]["Statement"])
    return list(statements)


def _matches(pattern: str, value: str) -> bool:
    """Match one IAM ARN or action pattern against a value.

    IAM wildcards are ``*`` (any sequence) and ``?`` (any single character) - deliberately NOT
    regular expressions and NOT shell globs, whose ``[...]`` classes IAM does not implement.
    ``fnmatch`` would treat ``[`` as a class opener and quietly change the meaning of an ARN
    containing one, so the translation is explicit.

    :param pattern: The policy's pattern.
    :param value: The action or resource being tested.
    :returns: Whether the pattern matches.
    """
    import re

    regex = "".join(
        ".*" if ch == "*" else "." if ch == "?" else re.escape(ch) for ch in pattern
    )
    return re.fullmatch(regex, value, flags=re.IGNORECASE) is not None


def decide(statements: list[dict[str, Any]], action: str, resource: str) -> str:
    """Return AWS's decision for one (action, resource) against a Deny-only policy.

    Implements the one rule that governs here: an explicit Deny in any applicable statement wins
    outright. Anything not denied is reported ``NotDenied`` rather than ``Allow`` - this policy
    grants nothing, so whether the principal may act depends on its *other* policies, and calling
    that "Allow" would overstate what has been checked.

    :param statements: The policy statements.
    :param action: e.g. ``secretsmanager:GetSecretValue``.
    :param resource: The target ARN.
    :returns: ``"Deny"`` or ``"NotDenied"``.
    """
    for st in statements:
        if st.get("Effect") != "Deny":
            continue
        actions = st.get("Action") or []
        actions = [actions] if isinstance(actions, str) else actions
        resources = st.get("Resource") or []
        resources = [resources] if isinstance(resources, str) else resources
        if any(_matches(a, action) for a in actions) and any(
            _matches(r, resource) for r in resources
        ):
            return "Deny"
    return "NotDenied"


def check_action_config(template_path: pathlib.Path) -> list[str]:
    """Assert the BudgetAction is configured to actually fire, and to fire as a cap.

    The policy can be perfect and the cap still do nothing, because whether it fires at all is
    decided by four fields that are easy to edit into harmlessness and that no schema check can
    object to - every value below is individually legal CloudFormation:

    ``ActionThreshold.Value == 100`` / ``Type == PERCENTAGE``
        A threshold quietly raised to 120% is a cap that never fires.
    ``ApprovalModel == AUTOMATIC``
        ``MANUAL`` means the action waits for a human to approve it - which, on the Saturday
        this exists for, is the same as having no cap at all.
    ``NotificationType == ACTUAL``
        ``FORECASTED`` would attach a DENY policy on a *prediction*, taking the service down for
        spend that has not happened.
    ``ActionType == APPLY_IAM_POLICY``
        Any other action type notifies rather than enforces.

    :param template_path: The template to read.
    :returns: A list of failure messages; empty when the configuration is a real cap.
    """
    import yaml

    class Loader(yaml.SafeLoader):
        """Keeps CloudFormation intrinsics as data."""

    Loader.add_constructor(
        "!Ref", lambda ldr, node: {"Ref": str(ldr.construct_scalar(node))}  # type: ignore[arg-type]
    )
    doc = yaml.load(template_path.read_text(), Loader=Loader)  # noqa: S506
    action = (doc.get("Resources") or {}).get("BudgetHardStop")
    if action is None:
        return ["BudgetHardStop resource is missing - there is no hard cap at all"]

    props = action["Properties"]
    failures: list[str] = []
    threshold = props.get("ActionThreshold") or {}

    if threshold.get("Value") != 100:
        failures.append(f"ActionThreshold.Value is {threshold.get('Value')!r}, not 100")
    if threshold.get("Type") != "PERCENTAGE":
        failures.append(f"ActionThreshold.Type is {threshold.get('Type')!r}, not PERCENTAGE")
    if props.get("ApprovalModel") != "AUTOMATIC":
        failures.append(
            f"ApprovalModel is {props.get('ApprovalModel')!r}, not AUTOMATIC - the cap would "
            "wait for a human and is therefore advisory"
        )
    if props.get("NotificationType") != "ACTUAL":
        failures.append(
            f"NotificationType is {props.get('NotificationType')!r}, not ACTUAL - a FORECASTED "
            "hard stop takes the service down for spend that has not happened"
        )
    if props.get("ActionType") != "APPLY_IAM_POLICY":
        failures.append(f"ActionType is {props.get('ActionType')!r}, not APPLY_IAM_POLICY")

    policy_arn = (props.get("Definition") or {}).get("IamActionDefinition", {}).get("PolicyArn")
    if not (isinstance(policy_arn, dict) and policy_arn.get("Ref") == "DenyLlmSpendPolicy"):
        failures.append(
            f"PolicyArn is {policy_arn!r}, not a !Ref to the in-template DenyLlmSpendPolicy - "
            "the cap would attach a policy this repository does not define or review"
        )
    return failures


def main() -> int:
    """Run the decision cases and report.

    :returns: A process exit code.
    """
    print("== the BudgetAction is configured as a cap, not as a notification ==")
    config_failures = check_action_config(TEMPLATE)
    for f in config_failures:
        print(f"  [FAIL] {f}")
    if not config_failures:
        print("  [ok ] threshold 100% ACTUAL, AUTOMATIC approval, APPLY_IAM_POLICY,")
        print("        attaching the policy this template itself defines")

    print("\n== the attached policy's decisions ==")
    statements = load_policy_statements(TEMPLATE)
    print(f"policy: DenyLlmSpendPolicy ({len(statements)} statements) from {TEMPLATE.name}\n")

    secret = (
        "arn:aws:secretsmanager:us-east-1:123456789012:"
        "secret:taxcalc/agent-svc/anthropic-AbC123"
    )
    other_secret = "arn:aws:secretsmanager:us-east-1:123456789012:secret:taxcalc/orders/db-XyZ789"
    proxy = "arn:aws:execute-api:us-east-1:123456789012:abc123/prod/POST/v1/completions"

    # (action, resource, expected, why this case is here)
    cases = [
        ("secretsmanager:GetSecretValue", secret, "Deny",
         "THE cap: ESO cannot refresh the Anthropic key, so no restarted pod gets one"),
        ("execute-api:Invoke", proxy, "Deny",
         "the proxy path, once model traffic is routed through it"),
        ("secretsmanager:GetSecretValue", other_secret, "NotDenied",
         "NEGATIVE CONTROL: an unrelated secret must still be readable - a cap that took the "
         "whole platform down would be an outage, not cost control"),
        ("sqs:ReceiveMessage", "arn:aws:sqs:us-east-1:123456789012:taxcalc-events", "NotDenied",
         "NEGATIVE CONTROL: unrelated services keep working"),
        ("secretsmanager:DescribeSecret", secret, "NotDenied",
         "NEGATIVE CONTROL: the deny is scoped to reading the VALUE, not to metadata - a "
         "broader action list would break every tool that lists secrets"),
    ]

    failures = len(config_failures)
    for action, resource, expected, why in cases:
        got = decide(statements, action, resource)
        ok = got == expected
        failures += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {action:38} -> {got:10} (expected {expected})")
        print(f"         {why}")

    print()
    if failures:
        print(f"FAILED: {failures} decision(s) did not match.")
        return 1
    print("VERIFIED: the cap is configured to fire, and denies exactly the two spend paths")
    print("          and nothing else.")
    print("NOT verified here: that AWS accepts the stack, that Budgets fires the action at")
    print("100%, or that account SCPs/permission boundaries alter the outcome.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
