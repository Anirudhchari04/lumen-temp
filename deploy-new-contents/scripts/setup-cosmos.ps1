# Long-term peer-identification fix: provision Cosmos DB and wire it into the Lumen Web App.
# Run this ONCE. Requires:
#   - az login
#   - Contributor role (or equivalent) on the Web App's resource group (activate via PIM)
#   - The Web App already deployed

param(
    [string]$ResourceGroup = "lumen-demo-rg",
    [string]$WebApp        = "lumen-demo",
    [string]$CosmosAccount = "lumendemo-cosmos-$(Get-Random -Maximum 9999)",
    [string]$Database      = "lumen-demo",
    [string]$Location      = "eastus"
)

Write-Host "1/6 Creating Cosmos DB account $CosmosAccount (serverless)..."
az cosmosdb create `
    --name $CosmosAccount `
    --resource-group $ResourceGroup `
    --locations "regionName=$Location" `
    --capabilities EnableServerless | Out-Null

Write-Host "2/6 Creating database + containers..."
az cosmosdb sql database create --account-name $CosmosAccount -g $ResourceGroup -n $Database | Out-Null
az cosmosdb sql container create --account-name $CosmosAccount -g $ResourceGroup -d $Database -n lumens        --partition-key-path "/id"      | Out-Null
az cosmosdb sql container create --account-name $CosmosAccount -g $ResourceGroup -d $Database -n chat_threads  --partition-key-path "/user_id" | Out-Null

Write-Host "3/6 Ensuring Web App has a system-assigned managed identity..."
$principalId = az webapp identity assign -g $ResourceGroup -n $WebApp --query principalId -o tsv

Write-Host "4/6 Granting the Web App MI data-plane access to Cosmos..."
$cosmosScope = az cosmosdb show -g $ResourceGroup -n $CosmosAccount --query id -o tsv
# "Cosmos DB Built-in Data Contributor" built-in role ID.
az cosmosdb sql role assignment create `
    --account-name $CosmosAccount `
    --resource-group $ResourceGroup `
    --role-definition-id "00000000-0000-0000-0000-000000000002" `
    --principal-id $principalId `
    --scope $cosmosScope | Out-Null

Write-Host "5/6 Setting Web App config (COSMOS_ENDPOINT)..."
$endpoint = az cosmosdb show -g $ResourceGroup -n $CosmosAccount --query documentEndpoint -o tsv
az webapp config appsettings set -g $ResourceGroup -n $WebApp --settings `
    COSMOS_ENDPOINT=$endpoint `
    COSMOS_DATABASE=$Database | Out-Null

Write-Host "6/6 Restarting Web App..."
az webapp restart -g $ResourceGroup -n $WebApp | Out-Null

Write-Host ""
Write-Host "Done. Cosmos endpoint: $endpoint"
Write-Host "Every signup is now persisted cross-instance and cross-restart."
Write-Host "Verify: open https://$WebApp.azurewebsites.net, log in, then 'Show my peers'."
