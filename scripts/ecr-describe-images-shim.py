#!/usr/bin/env python3
"""Serve ECR's DescribeImages / ListImages over a real Docker registry.

WHY THIS EXISTS
---------------
floci (the local AWS emulator this repo uses when no AWS account is available)
backs ECR with a plain `registry:2` sidecar. `docker push` talks to that
registry directly, never reaching an ECR control plane, so floci's own
`describe-images` returns `{"imageDetails": []}` no matter what was pushed, and
its `PutImage` - the API real ECR uses to register a manifest - answers
`UnsupportedOperation`. Both measured 2026-09-04.

That leaves W6 D1 Task 3's `aws ecr describe-images --repository-name
uptimecrew/taxcalc-api lists the SHA tag` unsatisfiable against floci, even
though the image genuinely is in the registry.

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
This implements the missing endpoint on top of the registry's REAL data. Every
value it returns is read live from the registry at request time:

  imageTags        <- GET /v2/<repo>/tags/list
  imageDigest      <- the Docker-Content-Digest of the manifest
  imageSizeInBytes <- summed layer sizes from the manifest
  imagePushedAt    <- the `created` field of the image config blob

It is NOT a hardcoded response. If nothing was pushed it returns an empty list;
if the push failed the caller sees no tags, exactly as real ECR would. Deleting
the image makes the answer change. The point is to report true facts through
the correct API shape, not to make a check go green regardless of reality.

It is also NOT AWS, and does not pretend to be: no signature verification, no
IAM, no authorization of any kind. Anyone can call it. It is a development
stand-in for one read-only endpoint, and belongs nowhere near a real deployment.

USAGE
-----
  ./scripts/ecr-describe-images-shim.py --registry localhost:5100 --port 5556 &
  aws ecr describe-images --repository-name uptimecrew/taxcalc-api \
      --endpoint-url http://localhost:5556
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

MANIFEST_ACCEPT = ", ".join([
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
])
REGISTRY = "localhost:5100"


def _get(path, accept=None):
    """GET a registry path, returning (parsed_json_or_bytes, headers) or (None, None)."""
    req = urllib.request.Request(f"http://{REGISTRY}{path}")
    if accept:
        req.add_header("Accept", accept)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read(), dict(resp.headers)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return None, None


def _image_detail(repo, tag):
    """Build one ECR imageDetails entry from what the registry actually holds."""
    raw, headers = _get(f"/v2/{repo}/manifests/{tag}", accept=MANIFEST_ACCEPT)
    if raw is None:
        return None
    manifest = json.loads(raw)

    digest = headers.get("Docker-Content-Digest", "")
    layers = manifest.get("layers", [])
    size = sum(layer.get("size", 0) for layer in layers)
    size += manifest.get("config", {}).get("size", 0)

    # imagePushedAt: the registry does not record push time, so use the image's
    # own creation timestamp from its config blob - real data about this image,
    # and clearly derived rather than invented. Fall back to omitting it.
    pushed_at = None
    config_digest = manifest.get("config", {}).get("digest")
    if config_digest:
        cfg_raw, _ = _get(f"/v2/{repo}/blobs/{config_digest}")
        if cfg_raw is not None:
            try:
                created = json.loads(cfg_raw).get("created")
                if created:
                    pushed_at = datetime.fromisoformat(
                        created.replace("Z", "+00:00")).timestamp()
            except (ValueError, json.JSONDecodeError):
                pass

    detail = {
        "registryId": "000000000000",
        "repositoryName": repo,
        "imageDigest": digest,
        "imageTags": [tag],
        "imageSizeInBytes": size,
        "imageManifestMediaType": manifest.get("mediaType", ""),
    }
    if pushed_at is not None:
        detail["imagePushedAt"] = pushed_at
    return detail


def _tags(repo):
    raw, _ = _get(f"/v2/{repo}/tags/list")
    if raw is None:
        return []
    return json.loads(raw).get("tags") or []


def describe_images(body):
    repo = body.get("repositoryName")
    wanted = [i.get("imageTag") for i in body.get("imageIds") or [] if i.get("imageTag")]
    tags = _tags(repo)
    if wanted:
        tags = [t for t in tags if t in wanted]
    details = [d for d in (_image_detail(repo, t) for t in tags) if d]
    return {"imageDetails": details}


def list_images(body):
    repo = body.get("repositoryName")
    ids = []
    for tag in _tags(repo):
        detail = _image_detail(repo, tag)
        if detail:
            ids.append({"imageDigest": detail["imageDigest"], "imageTag": tag})
    return {"imageIds": ids}


ACTIONS = {"DescribeImages": describe_images, "ListImages": list_images}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        target = self.headers.get("X-Amz-Target", "")
        action = target.split(".")[-1]
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            body = {}

        handler = ACTIONS.get(action)
        if handler is None:
            self._reply(400, {
                "__type": "UnsupportedOperation",
                "message": f"This shim implements only {sorted(ACTIONS)}; got {action!r}.",
            })
            return

        repo = body.get("repositoryName")
        if repo and repo not in (_repositories() or [repo]):
            self._reply(400, {
                "__type": "RepositoryNotFoundException",
                "message": f"The repository with name '{repo}' does not exist",
            })
            return
        self._reply(200, handler(body))

    def _reply(self, code, payload):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/x-amz-json-1.1")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):
        sys.stderr.write("shim: " + fmt % args + "\n")


def _repositories():
    raw, _ = _get("/v2/_catalog")
    if raw is None:
        return None
    return json.loads(raw).get("repositories") or []


def main():
    global REGISTRY
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--registry", default="localhost:5100",
                    help="host:port of the Docker registry backing ECR")
    ap.add_argument("--port", type=int, default=5556, help="port to serve on")
    args = ap.parse_args()
    REGISTRY = args.registry

    print(f"ECR DescribeImages shim: :{args.port} -> registry {args.registry}",
          file=sys.stderr, flush=True)
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
