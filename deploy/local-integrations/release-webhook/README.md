# Release watcher

The watcher polls the public `paperclipai/paperclip` releases API every 15
minutes. It sends a JSON object to a Paperclip routine webhook when it observes
a new published stable release. Drafts and prereleases are excluded. Promoting
a prerelease to stable makes it eligible for a notification.

The request uses HMAC-SHA256 over the exact body bytes in
`X-Hub-Signature-256`. A deterministic release ID supplies the delivery and
idempotency headers. The payload identifies its source as
`local_release_watcher` and contains public release metadata.

## Configuration

Use a routine webhook configured for GitHub HMAC authentication. Set these
non-secret environment references for the Compose command:

- `PAPERCLIP_RELEASE_WEBHOOK_URL`: the routine's HTTPS endpoint.
- `PAPERCLIP_RELEASE_SECRET_FILE`: an absolute path to its local signing file.

Provision the signing file through the operator's approved local workflow. Keep
its contents outside source control and logs. The process runs as UID 1000 and
needs read access to that file. A private parent directory and mode 0400 file
limit host access. Docker mounts the file read-only. A rotated file requires
container recreation so that Docker binds its current inode.

Run commands from this directory. Initial setup requires the routine to remain
in setup mode until the sender probe passes:

```sh
docker compose build release-watcher
docker compose run --rm --no-deps release-watcher --initialize
docker compose run --rm --no-deps release-watcher --probe
```

Initialization records existing stable releases without sending notifications.
The probe expects `test_received` and `routineStarted=false`. Complete webhook
setup in Paperclip after the probe. Then arm and start the sender:

```sh
docker compose run --rm --no-deps release-watcher --arm
docker compose up -d --no-deps release-watcher
```

Keep `/state` in its named volume. Initialization refuses an existing state
file. An absent state file stops normal operation without resetting history.
Probes are refused after the sender is armed. Never probe a live routine as a
connection test: an active routine can dispatch that request as work.

## Operation

```sh
docker compose ps release-watcher
docker compose logs --tail=50 release-watcher
docker compose exec -T release-watcher python /app/watcher.py --status
docker compose exec -T release-watcher python /app/watcher.py --health
```

API pages use ETags and pagination. Rate limit responses can extend the next
poll deadline. The pending delivery queue is saved before sending. Successful
routine dispatch removes an event from the queue. Retry uses its original ID,
which Paperclip deduplicates. A setup receipt, failed dispatch or suppressed run
retains the event and records an error for operator review.

The container has a read-only root filesystem, no published ports, no elevated
capabilities, bounded logs, and memory and CPU limits. Its health check fails
for a stale check or recorded error. The process continues its scheduled
retries. Docker health status alone does not restart a running container.

Stop the service and disable its Paperclip trigger to suspend automatic work.
Keep the state volume when recreating or rebuilding it. Resuming the sender
processes eligible releases published while it was stopped.

## Checks

```sh
python3 -m unittest discover -s . -p test_watcher.py -v
```

Tests cover release eligibility, promotion, deduplication, delivery retry,
persistent state, baseline behavior, ETag caching and signed HTTP requests.
The HTTP signing test uses an ephemeral key and a loopback server. It sends
nothing to a live Paperclip routine.

References: [release API](https://docs.github.com/en/rest/releases/releases),
[API request guidance](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api),
and [webhook signatures](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries).
