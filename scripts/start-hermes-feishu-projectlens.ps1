#Requires -Version 5.1
<#
.SYNOPSIS
  Start Hermes Feishu WebSocket gateway against a local ProjectLens API.

.DESCRIPTION
  Paths are no longer hardcoded. Defaults:
  - ProjectLens root = repo root (parent of scripts/)
  - Hermes root      = $env:HERMES_ROOT or sibling ../hermes-agent-main
  - HERMES_HOME      = $env:HERMES_HOME or LocalAppData/hermes
  - Python           = $env:PYTHON_EXE or `py -3.12` / `python`

.PARAMETER ProjectLensRoot
  Absolute path to this ProjectLens checkout.

.PARAMETER HermesRoot
  Absolute path to the Hermes agent checkout (must contain hermes_cli).

.PARAMETER HermesHome
  Hermes home directory (config.yaml / .env).

.PARAMETER PythonExe
  Python executable used for uvicorn and hermes_cli.

.PARAMETER ApiBaseUrl
  ProjectLens API base, default http://127.0.0.1:8000/api/v1

.PARAMETER TenantId
  Default tenant for Hermes ProjectLens tools.

.PARAMETER ProjectId
  Default project for Hermes ProjectLens tools (demo: payment).

.PARAMETER SkipApiStart
  Do not auto-start uvicorn if port 8000 is free.

.EXAMPLE
  .\scripts\start-hermes-feishu-projectlens.ps1

.EXAMPLE
  .\scripts\start-hermes-feishu-projectlens.ps1 `
    -HermesRoot "D:\src\hermes-agent-main" `
    -ProjectId "payment"
#>
[CmdletBinding()]
param(
    [string]$ProjectLensRoot = "",
    [string]$HermesRoot = "",
    [string]$HermesHome = "",
    [string]$PythonExe = "",
    [string]$ApiBaseUrl = "http://127.0.0.1:8000/api/v1",
    [string]$TenantId = "demo",
    [string]$ProjectId = "payment",
    [switch]$SkipApiStart
)

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

function Resolve-PythonExe {
    param([string]$Preferred)

    if ($Preferred -and (Test-Path $Preferred)) {
        return (Resolve-Path $Preferred).Path
    }
    if ($env:PYTHON_EXE -and (Test-Path $env:PYTHON_EXE)) {
        return (Resolve-Path $env:PYTHON_EXE).Path
    }

    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        try {
            $resolved = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $resolved) {
                return $resolved.Trim()
            }
        } catch { }
    }

    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        return $pythonCmd.Source
    }

    throw "Python not found. Pass -PythonExe or set PYTHON_EXE."
}

if (-not $ProjectLensRoot) {
    $ProjectLensRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
if (-not (Test-Path $ProjectLensRoot)) {
    throw "ProjectLensRoot not found: $ProjectLensRoot"
}

$projectLensEnv = Join-Path $ProjectLensRoot ".env"
$hermesRepoFromEnv = Import-DotEnvValue -Path $projectLensEnv -Key "PROJECT_LENS_HERMES_REPO"

if (-not $HermesRoot) {
    if ($env:HERMES_ROOT) {
        $HermesRoot = $env:HERMES_ROOT
    } elseif ($hermesRepoFromEnv) {
        $HermesRoot = $hermesRepoFromEnv
    } else {
        $HermesRoot = Join-Path (Split-Path $ProjectLensRoot -Parent) "hermes-agent-main"
    }
}
if (-not (Test-Path $HermesRoot)) {
    throw @"
HermesRoot not found: $HermesRoot

Clone Hermes next to this repo, or pass -HermesRoot / set HERMES_ROOT /
PROJECT_LENS_HERMES_REPO in .env. See docs/feishu-test-quickstart.md
"@
}

if (-not $HermesHome) {
    if ($env:HERMES_HOME) {
        $HermesHome = $env:HERMES_HOME
    } else {
        $HermesHome = Join-Path $env:LOCALAPPDATA "hermes"
    }
}

$PythonExe = Resolve-PythonExe -Preferred $PythonExe

$envTenant = Import-DotEnvValue -Path $projectLensEnv -Key "PROJECT_LENS_DEFAULT_TENANT_ID"
$envProject = Import-DotEnvValue -Path $projectLensEnv -Key "PROJECT_LENS_DEFAULT_PROJECT_ID"
if ($envTenant) { $TenantId = $envTenant }
if ($envProject) { $ProjectId = $envProject }

$appId = Import-DotEnvValue -Path $projectLensEnv -Key "PROJECT_LENS_FEISHU_APP_ID"
$appSecret = Import-DotEnvValue -Path $projectLensEnv -Key "PROJECT_LENS_FEISHU_APP_SECRET"
$verificationToken = Import-DotEnvValue -Path $projectLensEnv -Key "PROJECT_LENS_FEISHU_VERIFICATION_TOKEN"

if (-not $appId -or -not $appSecret) {
    throw "Missing PROJECT_LENS_FEISHU_APP_ID or PROJECT_LENS_FEISHU_APP_SECRET in $projectLensEnv"
}

Set-Location $HermesRoot

$env:HERMES_HOME = $HermesHome
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

$env:PROJECTLENS_API_BASE_URL = $ApiBaseUrl
$env:PROJECTLENS_DEFAULT_TENANT_ID = $TenantId
$env:PROJECTLENS_DEFAULT_PROJECT_ID = $ProjectId
$projectLensHermesProvider = Import-DotEnvValue -Path $projectLensEnv -Key "PROJECT_LENS_FEISHU_HERMES_PROVIDER"
$projectLensHermesModel = Import-DotEnvValue -Path $projectLensEnv -Key "PROJECT_LENS_FEISHU_HERMES_MODEL"
$projectLensHermesBaseUrl = Import-DotEnvValue -Path $projectLensEnv -Key "PROJECT_LENS_FEISHU_HERMES_BASE_URL"
$projectLensHermesApiKey = Import-DotEnvValue -Path $projectLensEnv -Key "PROJECT_LENS_FEISHU_HERMES_API_KEY"
$projectLensModelBaseUrl = Import-DotEnvValue -Path $projectLensEnv -Key "PROJECT_LENS_MODEL_OPENAI_BASE_URL"
$projectLensModelApiKey = Import-DotEnvValue -Path $projectLensEnv -Key "PROJECT_LENS_MODEL_OPENAI_API_KEY"
$projectLensDeepseekApiKey = Import-DotEnvValue -Path $projectLensEnv -Key "DEEPSEEK_API_KEY"
if ($projectLensHermesProvider) { $env:OPENAI_PROVIDER = $projectLensHermesProvider }
if ($projectLensHermesModel) { $env:OPENAI_MODEL = $projectLensHermesModel }
if ($projectLensHermesBaseUrl) { $env:OPENAI_BASE_URL = $projectLensHermesBaseUrl }
if ($projectLensHermesApiKey) { $env:OPENAI_API_KEY = $projectLensHermesApiKey }
if ($projectLensModelApiKey) { $env:OPENAI_API_KEY = $projectLensModelApiKey }
if ($projectLensDeepseekApiKey) { $env:DEEPSEEK_API_KEY = $projectLensDeepseekApiKey }

# Hermes resolves the primary model from config.yaml before environment
# variables. Keep that model selection aligned with ProjectLens without
# persisting the API key in the config file.
$hermesConfigPath = Join-Path $env:HERMES_HOME "config.yaml"
if ((Test-Path $hermesConfigPath) -and $projectLensHermesModel) {
    Copy-Item -Path $hermesConfigPath -Destination "$hermesConfigPath.projectlens-backup" -Force
    $hermesConfig = Get-Content $hermesConfigPath -Raw
    $configProvider = if ($projectLensHermesProvider -eq "deepseek") { "deepseek" } else { "openai-api" }
    $configBaseUrl = if ($projectLensHermesProvider -eq "deepseek") { "https://api.deepseek.com/v1" } else { $projectLensModelBaseUrl }
    $hermesConfig = $hermesConfig -replace '(?m)^  provider:.*$', "  provider: $configProvider"
    $hermesConfig = $hermesConfig -replace '(?m)^  default:.*$', "  default: $projectLensHermesModel"
    $hermesConfig = $hermesConfig -replace '(?m)^  api_mode:.*$', '  api_mode: chat_completions'
    if ($hermesConfig -match '(?m)^  base_url:') {
        $hermesConfig = $hermesConfig -replace '(?m)^  base_url:.*$', "  base_url: $configBaseUrl"
    } else {
        $hermesConfig = $hermesConfig -replace '(?m)^(  api_mode:.*)$', "`$1`r`n  base_url: $configBaseUrl"
    }
    Set-Content -Path $hermesConfigPath -Value $hermesConfig -Encoding utf8
}

# Hermes reloads its home .env inside the agent worker. Synchronize the
# OpenAI-compatible endpoint there too, otherwise stale DeepSeek variables
# override the process environment after startup.
if ((Test-Path $hermesEnv) -and $projectLensModelBaseUrl -and $projectLensModelApiKey) {
    Copy-Item -Path $hermesEnv -Destination "$hermesEnv.projectlens-backup" -Force
    $hermesEnvLines = [System.Collections.Generic.List[string]](Get-Content $hermesEnv)
    $envOverrides = @{
        "OPENAI_BASE_URL" = $projectLensModelBaseUrl
        "OPENAI_API_KEY" = $projectLensModelApiKey
        "OPENAI_MODEL" = $projectLensHermesModel
    }
    foreach ($key in $envOverrides.Keys) {
        $found = $false
        for ($index = 0; $index -lt $hermesEnvLines.Count; $index++) {
            if ($hermesEnvLines[$index] -match "^$([regex]::Escape($key))=") {
                $hermesEnvLines[$index] = "$key=$($envOverrides[$key])"
                $found = $true
                break
            }
        }
        if (-not $found) { $hermesEnvLines.Add("$key=$($envOverrides[$key])") }
    }
    Set-Content -Path $hermesEnv -Value $hermesEnvLines -Encoding utf8
}

if ((Test-Path $hermesEnv) -and $projectLensDeepseekApiKey) {
    $hermesEnvLines = [System.Collections.Generic.List[string]](Get-Content $hermesEnv)
    $found = $false
    for ($index = 0; $index -lt $hermesEnvLines.Count; $index++) {
        if ($hermesEnvLines[$index] -match '^DEEPSEEK_API_KEY=') {
            $hermesEnvLines[$index] = "DEEPSEEK_API_KEY=$projectLensDeepseekApiKey"
            $found = $true
            break
        }
    }
    if (-not $found) { $hermesEnvLines.Add("DEEPSEEK_API_KEY=$projectLensDeepseekApiKey") }
    Set-Content -Path $hermesEnv -Value $hermesEnvLines -Encoding utf8
}

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

# Keep the local ProjectLens API available before starting Hermes.
$apiUrl = "$ApiBaseUrl/integrations/feishu/status"
$portMatch = [regex]::Match($ApiBaseUrl, ':(\d+)')
$apiPort = if ($portMatch.Success) { [int]$portMatch.Groups[1].Value } else { 8000 }

if (-not $SkipApiStart) {
    $apiListening = Get-NetTCPConnection -LocalPort $apiPort -State Listen -ErrorAction SilentlyContinue
    if (-not $apiListening) {
        $apiOut = Join-Path $ProjectLensRoot "project-lens-api.out.log"
        $apiErr = Join-Path $ProjectLensRoot "project-lens-api.err.log"
        Start-Process -FilePath $PythonExe -WorkingDirectory $ProjectLensRoot -WindowStyle Hidden `
            -ArgumentList @("-m", "uvicorn", "project_lens.main:app", "--host", "127.0.0.1", "--port", "$apiPort") `
            -RedirectStandardOutput $apiOut -RedirectStandardError $apiErr | Out-Null
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            Start-Sleep -Milliseconds 500
            try {
                $status = Invoke-WebRequest -Uri $apiUrl -UseBasicParsing -TimeoutSec 2
                if ($status.StatusCode -eq 200) { break }
            } catch {
                if ($attempt -eq 19) { throw "ProjectLens API did not become ready at $apiUrl" }
            }
        }
    }
}

if ($verificationToken) {
    $env:FEISHU_VERIFICATION_TOKEN = $verificationToken
}

$openaiReady = [bool]$env:OPENAI_API_KEY
$modelHint = if ($env:OPENAI_MODEL) { $env:OPENAI_MODEL } else { "(see config.yaml model.default)" }
$baseHint = if ($env:OPENAI_BASE_URL) { $env:OPENAI_BASE_URL } else { "(default OpenAI)" }

Write-Host "Hermes Feishu Gateway for ProjectLens" -ForegroundColor Cyan
Write-Host "ProjectLens root: $ProjectLensRoot" -ForegroundColor Yellow
Write-Host "Hermes root:      $HermesRoot" -ForegroundColor Yellow
Write-Host "HERMES_HOME:      $HermesHome" -ForegroundColor Yellow
Write-Host "Python:           $PythonExe" -ForegroundColor Yellow
Write-Host "Mode: websocket" -ForegroundColor Yellow
Write-Host "ProjectLens API: $env:PROJECTLENS_API_BASE_URL" -ForegroundColor Yellow
Write-Host "ProjectLens default project: $env:PROJECTLENS_DEFAULT_TENANT_ID/$env:PROJECTLENS_DEFAULT_PROJECT_ID" -ForegroundColor Yellow
Write-Host "Feishu credentials loaded from ProjectLens .env (values hidden)." -ForegroundColor Yellow
Write-Host "OpenAI provider: key=$openaiReady base=$baseHint model=$modelHint" -ForegroundColor Yellow
if (-not $openaiReady) {
    Write-Host "WARNING: OPENAI_API_KEY missing — Feishu NL will fail provider auth." -ForegroundColor Red
}
Write-Host ""
Write-Host "In Feishu, @mention the bot and send:" -ForegroundColor Green
Write-Host "介绍一下这个项目" -ForegroundColor Green
Write-Host ""

& $PythonExe -m hermes_cli.main gateway run --force
