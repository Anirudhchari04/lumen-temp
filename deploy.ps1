# Lumen Demo — deploy to Azure App Service
# Run this after: az login
# Then: .\deploy.ps1

Write-Host "Building frontend..." -ForegroundColor Cyan
Set-Location "$PSScriptRoot\frontend"
npm run build

Write-Host "Packaging deploy zip..." -ForegroundColor Cyan
Set-Location $PSScriptRoot
# Build a correctly-rooted zip (requirements.txt + app/ + frontend/dist at root).
# `az webapp up` has been observed to omit requirements.txt, which makes Oryx
# fail with "Could not detect any platform" — so we package explicitly.
python scripts\make_deploy_zip.py

Write-Host "Deploying to Azure..." -ForegroundColor Cyan
az account set --subscription 9ab2d0c6-b2c7-4c73-937a-5b3093a61113
az webapp deploy --resource-group nexus-rg --name lumen-demo --src-path deploy-new.zip --type zip

Write-Host "Restarting app to pick up new code..." -ForegroundColor Cyan
az webapp restart --resource-group nexus-rg --name lumen-demo

Write-Host "Done! https://lumen-demo.azurewebsites.net" -ForegroundColor Green
