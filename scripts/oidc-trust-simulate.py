#!/usr/bin/env python3
"""Decide whether AWS would allow sts:AssumeRoleWithWebIdentity for a given
OIDC token against the trust policies committed in infra/oidc/.

This is NOT a mock of AWS. Mocking the account-side resources (an emulator
holding a role + trust policy) is worthless for this question, because
emulators return credentials for any AssumeRoleWithWebIdentity call without
evaluating the trust policy's conditions at all - so they pass just as
happily with a wrong `sub`, which is exactly the bug worth catching.

That is measured, not assumed. On 2026-09-03 floci was handed a forged token
(unsigned, iss=https://evil.example.com, sub=repo:someone-else/their-repo:
ref:refs/heads/main) and still returned working credentials for the
taxcalc-api-build-push role, reporting SubjectFromWebIdentityToken as the
placeholder "web-identity-subject" and Provider as "accounts.google.com" -
i.e. it never parsed the token at all. An emulator-backed CI run would
therefore print the exact "assumed-role/taxcalc-api-build-push/..." ARN that
Task 3's Done-When looks for even with the trust policy deleted. See
scripts/docker-oidc-verify.sh's header for the full transcript.

Instead this reproduces the decision procedure itself, which is small and
fully documented: for a web-identity assume, AWS checks that the token's
issuer matches the Principal.Federated provider, and that the `aud` and `sub`
claims satisfy the statement's Condition block under IAM's StringEquals /
StringLike semantics. Every input is already known - the claims were measured
by .github/workflows/oidc-probe.yml, and the policies are in this repo - so
the outcome is computable offline and deterministically.

What this DOES answer: given this token and this policy, is the decision
Allow or Deny - i.e. is the policy *logic* right.
What it does NOT answer: whether the provider/role/policy have actually been
created in an AWS account. That is an existence fact about resources, not a
logic question, and nothing offline can observe it.

Usage:
  scripts/oidc-trust-simulate.py --sub-prefix 'repo:ORG@1/REPO@2'
  scripts/oidc-trust-simulate.py --sub 'repo:ORG@1/REPO@2:environment:dev' \
      --policy infra/oidc/trust-policy-build.json
"""
import argparse
import fnmatch
import json
import pathlib
import sys

ISSUER = "https://token.actions.githubusercontent.com"
AUD_KEY = "token.actions.githubusercontent.com:aud"
SUB_KEY = "token.actions.githubusercontent.com:sub"
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _as_list(value):
    return value if isinstance(value, list) else [value]


def string_equals(claim, allowed):
    """IAM StringEquals: exact, case-sensitive. Any one value matching is enough."""
    return any(claim == a for a in _as_list(allowed))


def string_like(claim, allowed):
    """IAM StringLike: case-sensitive with `*` (any sequence) and `?` (one char).

    fnmatchcase implements exactly those two wildcards and does not treat
    `[...]` specially in a way that matters here - no committed policy uses
    brackets, and both are checked for below so a future one cannot slip past.
    """
    for pattern in _as_list(allowed):
        if "[" in pattern or "]" in pattern:
            raise ValueError(
                f"pattern {pattern!r} contains a bracket; fnmatch would treat it as a "
                "character class but IAM would not - refusing to guess"
            )
        if fnmatch.fnmatchcase(claim, pattern):
            return True
    return False


def evaluate(policy, sub, aud=("sts.amazonaws.com")):
    """Return (allowed, [reasons]) for a web-identity assume with these claims."""
    reasons = []
    for stmt in policy["Statement"]:
        if stmt.get("Effect") != "Allow":
            continue
        if "sts:AssumeRoleWithWebIdentity" not in _as_list(stmt.get("Action", [])):
            reasons.append("statement does not allow sts:AssumeRoleWithWebIdentity")
            continue

        federated = stmt.get("Principal", {}).get("Federated", "")
        if not federated.endswith("/token.actions.githubusercontent.com"):
            reasons.append(f"Principal.Federated {federated!r} is not the GitHub OIDC provider")
            continue

        cond = stmt.get("Condition", {})
        eq = cond.get("StringEquals", {})
        like = cond.get("StringLike", {})

        if AUD_KEY in eq and not string_equals(aud, eq[AUD_KEY]):
            reasons.append(f"aud {aud!r} != required {eq[AUD_KEY]!r}")
            continue

        # A sub condition MUST exist. Without one, any GitHub repo on earth
        # could assume the role - so treat its absence as a failure, not a pass.
        if SUB_KEY in eq:
            if not string_equals(sub, eq[SUB_KEY]):
                reasons.append(f"sub {sub!r} does not StringEquals {eq[SUB_KEY]!r}")
                continue
        elif SUB_KEY in like:
            if not string_like(sub, like[SUB_KEY]):
                reasons.append(f"sub {sub!r} does not StringLike {like[SUB_KEY]!r}")
                continue
        else:
            reasons.append("NO sub condition - policy would trust every GitHub repo")
            continue

        return True, [f"matched statement {stmt.get('Sid', '<no Sid>')!r}"]

    return False, reasons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sub", help="a full subject claim to test")
    ap.add_argument("--sub-prefix", help="test all three contexts derived from this prefix")
    ap.add_argument("--aud", default="sts.amazonaws.com")
    ap.add_argument("--policy", help="a single policy file (default: both committed ones)")
    args = ap.parse_args()

    build = REPO_ROOT / "infra/oidc/trust-policy-build.json"
    prod = REPO_ROOT / "infra/oidc/trust-policy-prod.json"

    if args.sub:
        cases = [(pathlib.Path(args.policy) if args.policy else build, args.sub)]
    elif args.sub_prefix:
        p = args.sub_prefix
        cases = [
            (build, f"{p}:environment:dev"),
            (build, f"{p}:ref:refs/heads/main"),
            (prod, f"{p}:environment:prod"),
            # Negative controls: these MUST be denied. A simulator that only
            # ever says Allow is as useless as an emulator that always says yes.
            (build, f"{p}:pull_request"),
            (prod, f"{p}:environment:dev"),
            (build, "repo:someone-else/their-repo:ref:refs/heads/main"),
        ]
    else:
        ap.error("pass --sub or --sub-prefix")

    expected_deny = {"pull_request", "someone-else"}
    failures = 0
    print(f"{'policy':<26} {'subject':<78} verdict")
    print("-" * 118)
    for path, sub in cases:
        policy = json.loads(path.read_text())
        allowed, reasons = evaluate(policy, sub, args.aud)
        should_deny = any(m in sub for m in expected_deny) or (
            path == prod and ":environment:prod" not in sub
        )
        ok = (not allowed) if should_deny else allowed
        failures += 0 if ok else 1
        mark = "ALLOW" if allowed else "DENY "
        flag = "" if ok else "   <-- UNEXPECTED"
        print(f"{path.name:<26} {sub:<78} {mark}{flag}")
        if not allowed and not should_deny:
            for r in reasons:
                print(f"{'':<26} reason: {r}")

    print()
    if failures:
        print(f"FAIL: {failures} case(s) did not match the expected decision")
        return 1
    print("PASS: every production subject is allowed, and every negative control is denied.")
    print("NOTE: this proves the policy LOGIC only. Whether the provider, roles and")
    print("      trust policies actually exist in an AWS account is a separate,")
    print("      account-side fact that no offline check can establish.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
