# Cognee Local

Cognee provides shared memory through its HTTP MCP endpoint. The image pins
Cognee 1.6.5 and MCP 0.5.7, with GLiNER graph extraction and Fastembed embeddings
on CPU. The embedding model is `BAAI/bge-small-en-v1.5`, with 384 dimensions.
Matching CPU PyTorch and torchvision packages support extractor imports.

SQLite metadata and session cache, LanceDB vectors, Ladybug graph files, input
documents and model caches live under `/cognee-storage` in a named volume.
Telemetry is disabled. This configuration uses no cloud model key. Recall
returns retrieved context for the calling agent to interpret.

## Deployment

Run from this directory:

```sh
docker compose build cognee
docker compose up -d --no-deps cognee
docker compose ps cognee
```

The host port is loopback-only. Set `COGNEE_HOST_PORT` to select another port.
Attach Paperclip to the same private Compose network and create a generic
remote MCP connection at `http://cognee:8000/mcp`. The service is shared and
unauthenticated inside that network. Keep it off public ingress and agent
worker networks. Dataset names organize memory; they do not isolate tenants.

Paperclip's connection-owned agent instructions provide the shared usage prompt
on authorized turns. Explicit per-agent installation and connection policies
control who receives tools and instructions. A connection-scoped block policy
is required when other profiles would grant broad access to excluded agents.
Keep company IDs, agent IDs, connection IDs, dataset choices and prompt text in
the deployment's configuration outside the checkout.

Use `recall` before substantive work when earlier context matters. Use
`remember` for concise durable facts and decisions after useful work. Omit
`session_id` for permanent memory. For long ingestion, use `background=true`
and poll `cognify_status`; a queued response does not confirm persistence.
Instructions are model guidance. Tool authorization remains enforced separately.

## Verification and storage

Run the synthetic memory check from the repository root, using the running
service as the only writer:

```sh
docker compose exec -T cognee python - < deploy/local-integrations/cognee-local/smoke.py
docker compose exec -T cognee python - recall < deploy/local-integrations/cognee-local/smoke.py
```

The first command writes the dedicated `paperclip_install_smoke` dataset. The
second only recalls it and can verify persistence after container recreation.
`COGNEE_TEST_DATASET` selects another dedicated test dataset. The script checks
both chunk and default retrieval and never prunes unrelated data.

Preserve the named volume across updates. Stop only Cognee before backing up
its embedded database files. Do not start another writer against that volume.
Normal container recreation preserves memory and downloaded models.

References: [MCP deployment](https://docs.cognee.ai/cognee-mcp/mcp-quickstart.md),
[keyless recall](https://docs.cognee.ai/guides/no-llm-remember-recall.md), and
[security settings](https://docs.cognee.ai/setup-configuration/security.md).
