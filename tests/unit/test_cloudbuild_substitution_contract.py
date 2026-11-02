"""Cloud Build substitution contract (#5053).

Two guards, both learned from a deploy that stayed red for five and a half months.

**First guard — a declared substitution must be redeemed.** Cloud Build rejects a
substitution it is handed but cannot place: ``invalid argument: key "_ENGINE_URL" in
the substitution data is not matched in the template``. That is what killed
``deploy-public-api.yml`` on every run from 2026-05-18 to 2026-09-24: the workflow
fetched the live backend URL and passed it as ``--substitutions=_ENGINE_URL=…`` while
``cloudbuild-public-api.yaml`` had stopped using ``$_ENGINE_URL`` anywhere in its
steps. The build aborted before step 0, so no image was ever built and the running
revision stayed at ``aaa-api-public-00024-prz`` from 2026-05-18.

The guard is swept over **every** ``cloudbuild*.yaml`` in the repository rather than
written against the one file that broke, because the next such file is the one nobody
is watching. Measured 2026-10-31 across all nine of them, ``_ENGINE_URL`` was the only
violation.

**Second guard — the service's env contract lives in the source, not in a revision.**
When the file was rewritten on 2026-10-03 the whole ``--update-env-vars`` /
``--update-secrets`` block disappeared with it. The live service kept its nine
variables only because ``gcloud run deploy`` leaves alone what it is not told to
change — the contract survived in a revision from 2026-05-18, not in anything
declared. A service recreated from this file would have come up without
``ENABLE_FIREBASE_AUTH=true`` and without its five secrets: a publicly reachable API
with its auth switch unset.

Parsed as TEXT, like the neighbouring ``test_cloudbuild_deploy_spec.py``: the deploy
step is a flat ``gcloud run deploy`` arg list, so substring assertions survive
reformatting and key reordering.
"""

import re
from pathlib import Path

import pytest

# VC-0 Platform Infrastructure (#3396): diese Tests prüfen den Deploy-Weg, nicht eine
# Stufe der Value Chain.
pytestmark = [pytest.mark.unit, pytest.mark.vc0]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PUBLIC_API = _REPO_ROOT / "cloudbuild-public-api.yaml"

#: The env variables the running revision carries, read off Cloud Run on 2026-10-31
#: (``aaa-api-public-00024-prz``). The file must declare each of them, so that a
#: recreated service is the same service.
_LIVE_PLAIN_ENV = (
    "ENGINE_URL",
    "ENABLE_FIREBASE_AUTH",
    "FIREBASE_PROJECT_ID",
    "GCP_PROJECT_ID",
)

#: The Artifact Registry repository the running revision pulls its image from, read
#: off Cloud Run on 2026-10-31: ``…/cloud-run-source-deploy/aaa-api-public@sha256:…``.
#: The build must push where the service pulls from — see #5058.
_LIVE_IMAGE_REPO = "cloud-run-source-deploy"

#: The secrets that same revision mounts. Names, not values — the values stay in
#: Secret Manager.
_LIVE_SECRETS = (
    "REDIS_URL",
    "ENGINE_API_KEY",
    "OAUTH_CLIENT_ID",
    "OAUTH_CLIENT_SECRET",
    "PROXY_ENGINE_SHARED_SECRET",
)


def _cloudbuild_files() -> list[Path]:
    """Every Cloud Build file in the repository — derived, not listed.

    A maintained list would be stale in the moment someone adds a build file, which
    is the moment the guard is needed.
    """
    gefunden = sorted(_REPO_ROOT.glob("cloudbuild*.yaml"))
    gefunden += sorted((_REPO_ROOT / "ai_trading_bot").glob("cloudbuild*.yaml"))
    return gefunden


def _unused_substitutions(text: str) -> list[str]:
    """Substitution keys the file declares but never places in its steps.

    ``substitution_option: ALLOW_LOOSE`` is Cloud Build's own opt-out from the strict
    match, so a file that sets it is not in breach.
    """
    if "ALLOW_LOOSE" in text:
        return []
    block = re.search(r"(?ms)^substitutions:\s*$(.*?)(?=^\S|\Z)", text)
    if not block:
        return []
    keys = re.findall(r"^\s{2,}(_[A-Z0-9_]+)\s*:", block.group(1), re.M)
    steps = text[: block.start()] + text[block.end() :]
    return [k for k in keys if not re.search(r"\$\{?" + k + r"\}?", steps)]


def test_cloudbuild_files_are_found():
    # A sweep that finds nothing would pass silently and guard nothing.
    dateien = _cloudbuild_files()
    assert len(dateien) >= 2, f"expected several cloudbuild files, found {dateien}"
    assert _PUBLIC_API in dateien


def test_every_declared_substitution_is_redeemed():
    verstoesse = {}
    for datei in _cloudbuild_files():
        unused = _unused_substitutions(datei.read_text(encoding="utf-8"))
        if unused:
            verstoesse[datei.relative_to(_REPO_ROOT).as_posix()] = unused
    assert not verstoesse, (
        "Cloud Build aborts the build when a substitution cannot be placed "
        f"(INVALID_ARGUMENT, 'not matched in the template'): {verstoesse}"
    )


def test_public_api_deploy_places_the_engine_url():
    # The workflow measures the backend's live URL on every run; if the template does
    # not place it, the measurement is both useless and fatal.
    text = _PUBLIC_API.read_text(encoding="utf-8")
    assert "ENGINE_URL=$_ENGINE_URL" in text


def test_public_api_pushes_where_the_service_pulls_from():
    """Build-Tag, Push, ``--image`` und ``images:`` nennen **ein** Repository, und zwar
    das, aus dem die laufende Revision zieht.

    Ob ein Artifact-Registry-Repository existiert, weiß nur GCP — das kann hier
    niemand prüfen. Prüfbar ist die Übereinstimmung mit dem laufenden Dienst, und die
    hätte gereicht: Am 03.10.2026 wurde der Pfad auf ``trading-bot`` geändert, das
    Repository des **Staging**-Builds in einem **anderen** Projekt. Im Prod-Projekt
    gibt es es nicht, und jeder Push scheiterte mit
    ``name unknown: Repository "trading-bot" not found`` (#5058).

    Die Menge wird verglichen, nicht einzelne Zeilen: Ein Build, der in ein Repository
    pusht und aus einem anderen deployt, wäre genauso falsch — nur stiller.
    """
    text = _PUBLIC_API.read_text(encoding="utf-8")
    repos = set(re.findall(r"docker\.pkg\.dev/\$PROJECT_ID/([^/]+)/", text))
    assert repos == {_LIVE_IMAGE_REPO}, (
        f"Build nennt {sorted(repos)}, die laufende Revision zieht aus "
        f"{_LIVE_IMAGE_REPO!r}"
    )


def test_public_api_declares_the_live_env_contract():
    text = _PUBLIC_API.read_text(encoding="utf-8")
    fehlend = [name for name in _LIVE_PLAIN_ENV if f"{name}=" not in text]
    assert not fehlend, f"env variables of the running revision not declared: {fehlend}"


def test_public_api_keeps_the_firebase_auth_switch_on():
    # The one value that must not merely be present but must be `true`: the public
    # proxy is reachable from the internet.
    assert "ENABLE_FIREBASE_AUTH=true" in _PUBLIC_API.read_text(encoding="utf-8")


def test_public_api_declares_its_secrets():
    text = _PUBLIC_API.read_text(encoding="utf-8")
    secrets = re.search(r"--update-secrets=([^\s]+)", text)
    assert secrets, "no --update-secrets in the deploy step"
    genannt = secrets.group(1)
    fehlend = [name for name in _LIVE_SECRETS if f"{name}=" not in genannt]
    assert not fehlend, f"secrets of the running revision not declared: {fehlend}"


def test_public_api_redis_url_is_a_secret_not_a_plain_value():
    # REDIS_URL carries credentials. It is mounted from Secret Manager, and the plain
    # env variable of the same name is removed so the two cannot disagree.
    text = _PUBLIC_API.read_text(encoding="utf-8")
    assert "--remove-env-vars=REDIS_URL" in text
    plain = re.search(r"--update-env-vars=([^\s]+)", text)
    assert plain, "no --update-env-vars in the deploy step"
    assert "REDIS_URL=" not in plain.group(1)
