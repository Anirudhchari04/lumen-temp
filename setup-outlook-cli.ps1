# Setup external Outlook app registration using Azure CLI
# Run this from PowerShell in your project directory

$ErrorActionPreference = "Stop"

Write-Host "🔧 Creating Outlook app registration..." -ForegroundColor Cyan

# Variables
$AppName = "Lumen Outlook"
$RedirectUri = "http://localhost:3000/auth/outlook-callback"  # Change if needed
$TenantId = "72f988bf-86f1-41af-91ab-2d7cd011db47"  # Your tenant

# Check if Azure CLI is installed
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Host "❌ Azure CLI not installed. Download from: https://aka.ms/azcli" -ForegroundColor Red
    exit 1
}

# 1. Create app registration
Write-Host "📝 Creating app registration: $AppName" -ForegroundColor Yellow
$ClientId = az ad app create `
    --display-name $AppName `
    --query appId -o tsv

Write-Host "✅ App created with CLIENT_ID: $ClientId" -ForegroundColor Green

# 2. Update app to allow public client flows
Write-Host "🔐 Enabling public client flow..." -ForegroundColor Yellow
az ad app update `
    --id $ClientId `
    --set isFallbackPublicClient=true | Out-Null

# 3. Add redirect URIs
Write-Host "🔗 Adding redirect URI: $RedirectUri" -ForegroundColor Yellow
az ad app update `
    --id $ClientId `
    --add "replyUrlsWithType=[{url: '$RedirectUri', type: 'PublicClient'}]" | Out-Null

# 4. Create service principal (optional)
Write-Host "👤 Creating service principal..." -ForegroundColor Yellow
try {
    az ad sp create --id $ClientId 2>$null | Out-Null
} catch {
    Write-Host "   Service principal already exists" -ForegroundColor Gray
}

# 5. Grant API permissions
Write-Host "📧 Adding Microsoft Graph permissions..." -ForegroundColor Yellow
$GraphId = "00000003-0000-0000-c000-000000000000"
$MailReadId = "570282fd-fa5c-430d-a7fd-fc8dc98a9b53"
$MailSendId = "024d486e-b451-40bb-833d-3b569c03d37e"

try {
    az ad app permission add `
        --id $ClientId `
        --api $GraphId `
        --api-permissions "$MailReadId=Scope" "$MailSendId=Scope" 2>$null | Out-Null
} catch {
    Write-Host "   Permissions already added" -ForegroundColor Gray
}

Write-Host ""
Write-Host "=========================================" -ForegroundColor Green
Write-Host "✅ Setup Complete!" -ForegroundColor Green
Write-Host "=========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Your CLIENT_ID:" -ForegroundColor Cyan
Write-Host "  $ClientId" -ForegroundColor White
Write-Host ""
Write-Host "Add this to your .env file:" -ForegroundColor Cyan
Write-Host "  EXTERNAL_OUTLOOK_CLIENT_ID=$ClientId" -ForegroundColor White
Write-Host ""
Write-Host "Redirect URI registered:" -ForegroundColor Cyan
Write-Host "  $RedirectUri" -ForegroundColor White
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Create/edit .env file in your project root"
Write-Host "  2. Add: EXTERNAL_OUTLOOK_CLIENT_ID=$ClientId"
Write-Host "  3. Restart your dev server"
Write-Host "  4. (Optional) Grant admin consent in Azure Portal if needed"
Write-Host ""
Write-Host "To test:" -ForegroundColor Cyan
Write-Host "  curl http://localhost:8000/auth/external-outlook-config" -ForegroundColor Gray
Write-Host ""

# Optionally save to .env
$EnvFile = ".env"
if (Test-Path $EnvFile) {
    Write-Host "📝 Updating $EnvFile..." -ForegroundColor Yellow
    $EnvContent = Get-Content $EnvFile -Raw
    if ($EnvContent -match "EXTERNAL_OUTLOOK_CLIENT_ID") {
        $EnvContent = $EnvContent -replace "EXTERNAL_OUTLOOK_CLIENT_ID=.*", "EXTERNAL_OUTLOOK_CLIENT_ID=$ClientId"
    } else {
        $EnvContent += "`nEXTERNAL_OUTLOOK_CLIENT_ID=$ClientId`n"
    }
    Set-Content $EnvFile -Value $EnvContent
    Write-Host "✅ .env updated" -ForegroundColor Green
} else {
    Write-Host "⚠️  No .env found. Create one with:" -ForegroundColor Yellow
    Write-Host "  EXTERNAL_OUTLOOK_CLIENT_ID=$ClientId" -ForegroundColor Gray
}
