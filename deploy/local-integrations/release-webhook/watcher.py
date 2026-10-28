"""Poll public releases; send durable, idempotent, HMAC-signed notifications."""
import argparse
import datetime as dt
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

REPO = "paperclipai/paperclip"
API = f"https://api.github.com/repos/{REPO}/releases?per_page=100"
ENDPOINT = os.getenv("WEBHOOK_URL")
STATE = Path(os.getenv("STATE_DIRECTORY", "/state"))
SECRET = Path(os.getenv("WEBHOOK_SECRET_FILE", "/run/secrets/release_webhook_secret"))
INTERVAL = max(60, int(os.getenv("POLL_INTERVAL_SECONDS", "900")))


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def log(event, **details):
    print(json.dumps({"time": now(), "event": event, **details}), flush=True)


class WatchError(Exception):
    def __init__(self, code, delay=0):
        super().__init__(code)
        self.delay = delay


def load():
    value = json.loads((STATE / "state.json").read_text())
    if value.get("version") != 1 or value.get("repository") != REPO:
        raise WatchError("state_invalid")
    return value


def save(value):
    temporary = STATE / "state.json.tmp"
    with open(temporary, "w", opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(STATE / "state.json")
    descriptor = os.open(STATE, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fetch_releases(cache):
    result, new_cache, url = [], {}, API
    # Follow all release pages, including cached pages; this also notices a
    # previously prerelease version being promoted to stable on an older page.
    for _ in range(100):
        previous = cache.get(url, {})
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "Paperclip-local-release-watcher/1.0"}
        if previous.get("etag"):
            headers["If-None-Match"] = previous["etag"]
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
                releases = json.load(response)
                if not isinstance(releases, list):
                    raise WatchError("github_invalid_response")
                links = urllib.request.parse_http_list(response.headers.get("Link", ""))
                next_url = None
                for link in links:
                    if 'rel="next"' in link:
                        next_url = link.split(">", 1)[0].lstrip("<")
                page = {"etag": response.headers.get("ETag"), "releases": releases, "next": next_url}
        except urllib.error.HTTPError as error:
            if error.code == 304 and previous:
                page = previous
            else:
                delay = 0
                try:
                    delay = max(int(error.headers.get("Retry-After", "0")),
                                int(error.headers.get("X-RateLimit-Reset", "0")) - int(time.time())
                                if error.headers.get("X-RateLimit-Remaining") == "0" else 0)
                except ValueError:
                    pass
                raise WatchError(f"github_http_{error.code}", delay) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise WatchError("github_unreachable") from None
        new_cache[url] = page
        result.extend(page["releases"])
        url = page.get("next")
        if not url:
            return result, new_cache
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != "api.github.com" or parsed.path != f"/repos/{REPO}/releases":
            raise WatchError("github_pagination_invalid")
    raise WatchError("github_pagination_limit")


def stable(releases):
    return sorted((r for r in releases if isinstance(r.get("id"), int)
                   and not r.get("draft") and not r.get("prerelease") and r.get("published_at")),
                  key=lambda r: (r["published_at"], r["id"]))


def stage(state, releases, cache):
    seen = set(state["seen_release_ids"])
    for release in stable(releases):
        if release["id"] in seen:
            continue
        # Persist the exact payload before any delivery; retries retain its ID.
        summary = {k: release.get(k) for k in ("id", "tag_name", "name", "html_url", "published_at", "prerelease", "draft")}
        state["pending"].append({
            "delivery_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://github.com/{REPO}/release/{release['id']}")),
            "payload": {"source": "local_release_watcher", "action": "published",
                        "repository": {"full_name": REPO}, "release": summary, "detected_at": now()},
        })
        seen.add(release["id"])
    state["seen_release_ids"] = sorted(seen)
    state["cache"] = cache
    state["last_github_check_at"] = now()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post(payload, delivery_id, event="release"):
    if not ENDPOINT:
        raise WatchError("webhook_url_required")
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    key = SECRET.read_bytes().strip()
    if not key:
        raise WatchError("webhook_secret_missing")
    request = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
        "Content-Type": "application/json", "User-Agent": "Paperclip-local-release-watcher/1.0",
        "X-GitHub-Event": event, "X-GitHub-Delivery": delivery_id,
        "Idempotency-Key": delivery_id,
        "X-Hub-Signature-256": "sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest(),
    })
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=45) as response:
            result = json.load(response)
            if response.status != 202 or not isinstance(result, dict):
                raise WatchError("webhook_invalid_response")
            return result
    except urllib.error.HTTPError as error:
        raise WatchError(f"webhook_http_{error.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise WatchError("webhook_unreachable") from None


def deliver(state, send=post, persist=save):
    while state["pending"]:
        event = state["pending"][0]
        response = send(event["payload"], event["delivery_id"])
        # A setup receipt, failed dispatch or suppression is not a successful
        # update-check run. Retain the pending notification for operator review.
        if response.get("status") not in {"issue_created", "coalesced"} or not response.get("id"):
            raise WatchError("webhook_not_dispatched")
        state["last_delivery"] = {"delivery_id": event["delivery_id"], "release_id": event["payload"]["release"]["id"],
                                  "tag": event["payload"]["release"]["tag_name"], "routine_run_id": response["id"], "at": now()}
        state["pending"].pop(0)
        persist(state)
        log("release_delivered", **state["last_delivery"])


def cycle(state):
    if not state.get("armed"):
        raise WatchError("watcher_not_armed")
    # Retry already queued deliveries even when GitHub is temporarily offline.
    deliver(state)
    releases, cache = fetch_releases(state.get("cache", {}))
    stage(state, releases, cache)
    save(state)
    deliver(state)
    state.update(last_successful_check_at=now(), last_error=None)
    save(state)
    log("check_complete", stable_release_count=len(stable(releases)), pending=len(state["pending"]))


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    for flag in ("initialize", "probe", "arm", "check", "loop", "health", "status"):
        group.add_argument("--" + flag, action="store_true")
    args = parser.parse_args()
    if (args.loop or args.check or args.probe) and not ENDPOINT:
        raise WatchError("webhook_url_required")
    if args.health or args.status:
        state = load()
        if args.status:
            print(json.dumps({k: v for k, v in state.items() if k != "cache"}, sort_keys=True))
            return
        timestamp = dt.datetime.fromisoformat(state.get("last_successful_check_at", state["initialized_at"]))
        healthy = state.get("armed") and not state.get("last_error") and (dt.datetime.now(dt.timezone.utc) - timestamp).total_seconds() < INTERVAL * 2 + 120
        raise SystemExit(0 if healthy else 1)
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.initialize:
            if (STATE / "state.json").exists():
                raise WatchError("state_already_initialized")
            releases, cache = fetch_releases({})
            selected = stable(releases)
            if not selected:
                raise WatchError("no_stable_releases_for_baseline")
            state = {"version": 1, "repository": REPO, "initialized_at": now(), "armed": False,
                     "seen_release_ids": sorted(r["id"] for r in selected), "pending": [], "cache": cache,
                     "baseline_tag": selected[-1]["tag_name"]}
            save(state)
            log("baseline_recorded", baseline_tag=state["baseline_tag"], historical_stable_releases=len(selected), notifications_sent=0)
            return
        state = load()  # A lost volume fails closed; never silently re-baseline.
        if args.probe:
            if state.get("armed"):
                raise WatchError("probe_requires_unarmed_watcher")
            result = post({"source": "local_release_watcher", "action": "setup_test", "repository": {"full_name": REPO}}, str(uuid.uuid4()), "ping")
            if result.get("status") != "test_received" or result.get("routineStarted") is not False:
                raise WatchError("probe_did_not_receive_setup_receipt")
            state["setup_probe_passed_at"] = now()
            save(state)
            log("setup_probe_passed", routine_started=False)
            return
        if args.arm:
            if not state.get("setup_probe_passed_at"):
                raise WatchError("successful_setup_probe_required")
            state["armed"] = True
            save(state)
            log("watcher_armed", baseline_tag=state["baseline_tag"])
            return
        if args.check:
            cycle(state)
            return
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        while not stop.is_set():
            delay = INTERVAL
            try:
                cycle(state)
            except Exception as error:
                # Error strings/bodies/headers are deliberately not logged.
                code = str(error) if isinstance(error, WatchError) else type(error).__name__
                state["last_error"] = {"code": code, "at": now()}
                save(state)
                log("check_failed", code=code, pending=len(state["pending"]))
                delay = max(INTERVAL, getattr(error, "delay", 0))
            stop.wait(delay)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        log("watcher_stopped", code=str(error) if isinstance(error, WatchError) else type(error).__name__)
        sys.exit(1)
