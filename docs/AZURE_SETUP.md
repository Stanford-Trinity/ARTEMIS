# Azure OpenAI Setup Guide

This guide explains how to configure ARTEMIS to use Azure OpenAI API instead of OpenRouter or OpenAI direct API.

## Prerequisites

1. An Azure subscription with Azure OpenAI Service access
2. Azure OpenAI resource created in Azure Portal
3. Model deployments created (e.g., gpt-4, gpt-4-turbo, gpt-35-turbo)
4. Azure OpenAI API key and endpoint URL

## Configuration Steps

### 1. Environment Variables Setup

Create or edit your `.env` file in the project root:

```bash
# Azure OpenAI API Configuration
AZURE_OPENAI_API_KEY=your-azure-openai-api-key-here
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_API_VERSION=2024-10-01-preview

# Optional: Override default models with your Azure deployment names
SUPERVISOR_MODEL=gpt-4
SUMMARIZATION_MODEL=gpt-4
ROUTER_MODEL=gpt-4
TODO_GENERATOR_AZURE_MODEL=gpt-4

# Optional: Specify available models for model switching (use your deployment names)
AZURE_AVAILABLE_MODELS=gpt-4,gpt-4-turbo,gpt-35-turbo
```

**Important Notes:**
- `AZURE_OPENAI_ENDPOINT`: Your Azure resource endpoint (find in Azure Portal)
- `AZURE_OPENAI_API_KEY`: Your API key from Azure Portal
- `AZURE_OPENAI_API_VERSION`: API version (default: `2024-10-01-preview`)
- Model names: Use your **deployment names** from Azure, not model names (e.g., if you deployed GPT-4 with deployment name "my-gpt4", use "my-gpt4")

### 2. Codex Binary Configuration (Rust Side)

The Codex binary needs to be configured to use Azure OpenAI. Create or edit `~/.codex/config.toml`:

```toml
# Set Azure as the default model provider
model_provider = "azure"

# Define the Azure provider configuration
[model_providers.azure]
name = "Azure OpenAI"
base_url = "https://your-resource.openai.azure.com/openai"
env_key = "AZURE_OPENAI_API_KEY"
wire_api = "chat"

# Azure requires api-version as a query parameter
[model_providers.azure.query_params]
api-version = "2024-10-01-preview"
```

**Configuration Explanation:**
- `model_provider`: Set to "azure" to use Azure by default
- `base_url`: Your Azure OpenAI endpoint + "/openai" suffix
- `env_key`: Environment variable name for the API key
- `wire_api`: Must be "chat" (Azure uses Chat Completions API)
- `query_params`: Azure requires `api-version` as a query parameter

### 3. Model Deployment Names

Azure OpenAI uses **deployment names** instead of model names. When you create a deployment in Azure Portal, you specify:
- **Model**: The base model (e.g., gpt-4, gpt-35-turbo)
- **Deployment Name**: The name you use in API calls (e.g., "my-gpt4", "production-gpt4")

**Always use your deployment names** in the environment variables and when running ARTEMIS.

Example:
```bash
# If your Azure deployments are named:
# - "gpt4-deployment" for GPT-4
# - "gpt4-turbo-deployment" for GPT-4 Turbo
# - "gpt35-deployment" for GPT-3.5 Turbo

# Set these in your .env:
SUPERVISOR_MODEL=gpt4-deployment
SUMMARIZATION_MODEL=gpt4-deployment
ROUTER_MODEL=gpt35-deployment
AZURE_AVAILABLE_MODELS=gpt4-deployment,gpt4-turbo-deployment,gpt35-deployment
```

## Verification

### Test Python Supervisor

Verify the Python supervisor can connect to Azure:

```bash
# This should show "✅ Azure OpenAI API key found"
python -m supervisor.supervisor --config-file examples/example-config.yaml --duration 1
```

### Test Codex Binary

Verify the Rust binary can connect to Azure:

```bash
# Set the provider and test
export AZURE_OPENAI_API_KEY="your-key"
codex --model-provider azure --model your-deployment-name "Test prompt"
```

## Troubleshooting

### Error: "API key not found"
- Verify `AZURE_OPENAI_API_KEY` is set in your `.env` file
- Ensure the `.env` file is in the project root directory
- Try running `source .env` or restarting your shell

### Error: "AZURE_OPENAI_ENDPOINT is required"
- Set `AZURE_OPENAI_ENDPOINT` in your `.env` file
- Format: `https://your-resource-name.openai.azure.com` (no trailing slash)

### Error: "Deployment not found" or 404 errors
- Verify your deployment names match exactly (case-sensitive)
- Check Azure Portal to see your actual deployment names
- Ensure deployments are in "Succeeded" state in Azure

### Error: API version errors
- Update `AZURE_OPENAI_API_VERSION` to a supported version
- Check [Azure OpenAI API versions](https://learn.microsoft.com/en-us/azure/ai-services/openai/reference#api-versions) for valid versions
- Current default: `2024-10-01-preview`

### Model switching issues
- Set `AZURE_AVAILABLE_MODELS` with comma-separated deployment names
- Do not include spaces: `model1,model2,model3`
- Use actual deployment names from your Azure account

## Comparison: OpenRouter vs OpenAI vs Azure

| Feature | OpenRouter | OpenAI Direct | Azure OpenAI |
|---------|-----------|---------------|--------------|
| API Key Env Var | `OPENROUTER_API_KEY` | `OPENAI_API_KEY` | `AZURE_OPENAI_API_KEY` |
| Endpoint | `https://openrouter.ai/api/v1` | `https://api.openai.com/v1` | Custom Azure endpoint |
| Model Format | `openai/gpt-4` | `gpt-4` | Deployment name (e.g., `my-gpt4`) |
| API Version | Not required | Not required | Required (query param) |
| Wire API | Chat Completions | Responses API | Chat Completions |
| Config Required | No | No | Yes (config.toml) |

## Advanced Configuration

### Multiple Azure Resources

If you have multiple Azure OpenAI resources, you can define multiple providers:

```toml
model_provider = "azure-prod"

[model_providers.azure-prod]
name = "Azure Production"
base_url = "https://prod-resource.openai.azure.com/openai"
env_key = "AZURE_PROD_API_KEY"
wire_api = "chat"

[model_providers.azure-prod.query_params]
api-version = "2024-10-01-preview"

[model_providers.azure-dev]
name = "Azure Development"
base_url = "https://dev-resource.openai.azure.com/openai"
env_key = "AZURE_DEV_API_KEY"
wire_api = "chat"

[model_providers.azure-dev.query_params]
api-version = "2024-10-01-preview"
```

### Custom HTTP Headers

Add custom headers if needed:

```toml
[model_providers.azure]
name = "Azure OpenAI"
base_url = "https://your-resource.openai.azure.com/openai"
env_key = "AZURE_OPENAI_API_KEY"
wire_api = "chat"

[model_providers.azure.query_params]
api-version = "2024-10-01-preview"

[model_providers.azure.http_headers]
X-Custom-Header = "custom-value"

[model_providers.azure.env_http_headers]
X-Environment-Header = "CUSTOM_ENV_VAR"
```

## Resources

- [Azure OpenAI Service Documentation](https://learn.microsoft.com/en-us/azure/ai-services/openai/)
- [Azure OpenAI Quickstart](https://learn.microsoft.com/en-us/azure/ai-services/openai/quickstart)
- [Azure OpenAI API Reference](https://learn.microsoft.com/en-us/azure/ai-services/openai/reference)
- [Model Deployments](https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/create-resource)

## Support

If you encounter issues not covered in this guide:
1. Check the Azure Portal for deployment status
2. Verify API key permissions and quotas
3. Review Azure OpenAI service logs
4. Open an issue in the ARTEMIS repository with error details
