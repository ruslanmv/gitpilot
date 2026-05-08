# mcp-milvus-server (mirror)

Read-only Milvus MCP server. Source:
<https://github.com/ruslanmv/milvus-admin-ui>, subdirectory `mcp-server/`.

## Tools GitPilot calls

| Tool | Used by |
|------|---------|
| `milvus.list_collections` | Explorer |
| `milvus.describe_collection` | Coder, Test Runner |
| `milvus.describe_index` | Reviewer |
| `milvus.search` / `milvus.hybrid_search` | Coder (RAG samples) |
| `milvus.validate_index_config` | Reviewer |
| `milvus.generate_ingestion_code` | Coder |
| `milvus.generate_rag_pipeline_context` | Coder |
| `milvus.generate_test_vectors` | Test Runner |

## Helpers in `gitpilot.mcp_plugin.agent_hooks`

- `coder_describe_collection(client, collection)`
- `test_runner_test_vectors(client, dimension, num_vectors=...)`
