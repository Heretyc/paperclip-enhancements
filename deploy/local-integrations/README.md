# Local integrations

This directory owns the source for two optional Paperclip deployment services:

- `cognee-local/` provides local graph memory through HTTP MCP.
- `release-webhook/` sends signed routine events for new stable releases.

Each service has a Dockerfile, a Compose definition, operating instructions and
focused checks. They use Paperclip's connection and routine contracts. They do
not change the application server, database schema or agent execution harness.

Deployment configuration selects endpoint URLs, instance identities, connection
access and secret file locations. Keep that configuration, credentials, runtime
state and verification receipts outside this checkout. Keep persistent volumes
when recreating containers. A source checkout contains no live instance data.

Changes belong on focused commits in `Heretyc/paperclip-enhancements`. Pull
requests target `master`. Release synchronization must retain these services and
their volumes while applying the fork's reviewed changes.

Run the release watcher checks from the repository root:

```sh
python3 -m unittest discover -s deploy/local-integrations/release-webhook -v
```

Build and configure only the selected service. Each service README describes its
inputs and its checks. The files are independent of the application build.
