$ErrorActionPreference = "Stop"

function Import-DotEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Key
    )

    if (-not (Test-Path $Path)) {
        return $null
    }

    foreach ($line in Get-Content $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }
        $prefix = "$Key="
        if ($trimmed.StartsWith($prefix)) {
            $value = $trimmed.Substring($prefix.Length).Trim()
            if (
                ($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'"))
            ) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            return $value
        }
    }
    return $null
}

$projectLensRoot = "C:\Users\Administrator\Desktop\project-lens"
$hermesRoot = "C:\Users\Administrator\Desktop\hermes-agent-main"
$projectLensEnv = Join-Path $projectLensRoot ".env"

$appId = Import-DotEnvValue -Path $projectLensEnv -Key "PROJECT_LENS_FEISHU_APP_ID"
$appSecret = Import-DotEnvValue -Path $projectLensEnv -Key "PROJECT_LENS_FEISHU_APP_SECRET"
$verificationToken = Import-DotEnvValue -Path $projectLensEnv -Key "PROJECT_LENS_FEISHU_VERIFICATION_TOKEN"

if (-not $appId -or -not $appSecret) {
    throw "Missing PROJECT_LENS_FEISHU_APP_ID or PROJECT_LENS_FEISHU_APP_SECRET in $projectLensEnv"
}

Set-Location $hermesRoot

$env:HERMES_HOME = "C:\Users\Administrator\AppData\Local\hermes"
$hermesEnv = Join-Path $env:HERMES_HOME ".env"

# Load Hermes home .env so OPENAI_* / FEISHU_* persist across restarts.
# Feishu app credentials below still prefer ProjectLens .env as source of truth.
if (Test-Path $hermesEnv) {
    foreach ($line in Get-Content $hermesEnv) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
        $eq = $trimmed.IndexOf("=")
        if ($eq -lt 1) { continue }
        $key = $trimmed.Substring(0, $eq)
        $value = $trimmed.Substring($eq + 1)
        if (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        Set-Item -Path "Env:$key" -Value $value
    }
}

$env:PROJECTLENS_API_BASE_URL = "http://127.0.0.1:8000/api/v1"
$env:PROJECTLENS_DEFAULT_TENANT_ID = "demo"
$env:PROJECTLENS_DEFAULT_PROJECT_ID = "payment"

$env:FEISHU_APP_ID = $appId
$env:FEISHU_APP_SECRET = $appSecret
$env:FEISHU_DOMAIN = "feishu"
$env:FEISHU_CONNECTION_MODE = "websocket"
$env:FEISHU_GROUP_POLICY = "open"
if (-not $env:FEISHU_REQUIRE_MENTION) {
    $env:FEISHU_REQUIRE_MENTION = "true"
}
$env:FEISHU_ALLOW_ALL_USERS = "true"
$env:GATEWAY_ALLOW_ALL_USERS = "true"

if ($verificationToken) {
    $env:FEISHU_VERIFICATION_TOKEN = $verificationToken
}

$openaiReady = [bool]$env:OPENAI_API_KEY
$modelHint = if ($env:OPENAI_MODEL) { $env:OPENAI_MODEL } else { "(see config.yaml model.default)" }
$baseHint = if ($env:OPENAI_BASE_URL) { $env:OPENAI_BASE_URL } else { "(default OpenAI)" }

Write-Host "Hermes Feishu Gateway for ProjectLens" -ForegroundColor Cyan
Write-Host "Mode: websocket" -ForegroundColor Yellow
Write-Host "ProjectLens API: $env:PROJECTLENS_API_BASE_URL" -ForegroundColor Yellow
Write-Host "ProjectLens default project: $env:PROJECTLENS_DEFAULT_TENANT_ID/$env:PROJECTLENS_DEFAULT_PROJECT_ID" -ForegroundColor Yellow
Write-Host "Feishu credentials loaded from ProjectLens .env (values hidden)." -ForegroundColor Yellow
Write-Host "OpenAI provider: key=$openaiReady base=$baseHint model=$modelHint" -ForegroundColor Yellow
if (-not $openaiReady) {
    Write-Host "WARNING: OPENAI_API_KEY missing in $hermesEnv — Feishu NL will fail provider auth." -ForegroundColor Red
}
Write-Host ""
Write-Host "In Feishu, @mention the bot and send:" -ForegroundColor Green
Write-Host "/project What is this project?" -ForegroundColor Green
Write-Host "Or a short natural-language ping to verify the LLM provider." -ForegroundColor Green
Write-Host ""

& "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe" -m hermes_cli.main gateway run --force
