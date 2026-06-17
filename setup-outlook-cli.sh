#!/bin/bash
# Setup external Outlook app registration using Azure CLI
# Run this from your project directory

set -e

echo "🔧 Creating Outlook app registration..."

# Variables
APP_NAME="Lumen Outlook"
REDIRECT_URI="http://localhost:3000/auth/outlook-callback"  # Change if needed
TENANT_ID="72f988bf-86f1-41af-91ab-2d7cd011db47"  # Your tenant

# 1. Create app registration
echo "📝 Creating app registration: $APP_NAME"
APP_RESPONSE=$(az ad app create \
  --display-name "$APP_NAME" \
  --query appId -o tsv)

CLIENT_ID=$APP_RESPONSE
echo "✅ App created with CLIENT_ID: $CLIENT_ID"

# 2. Update app to allow public client flows
echo "🔐 Enabling public client flow..."
az ad app update \
  --id "$CLIENT_ID" \
  --set isFallbackPublicClient=true

# 3. Add redirect URIs
echo "🔗 Adding redirect URI: $REDIRECT_URI"
az ad app update \
  --id "$CLIENT_ID" \
  --add replyUrlsWithType "[{url: \"$REDIRECT_URI\", type: \"PublicClient\"}]"

# 4. Create service principal (optional, but good practice)
echo "👤 Creating service principal..."
az ad sp create --id "$CLIENT_ID" 2>/dev/null || echo "Service principal already exists"

# 5. Grant API permissions (Mail.Read, Mail.Send)
echo "📧 Adding Microsoft Graph permissions..."
GRAPH_ID="00000003-0000-0000-c000-000000000000"

# Mail.Read (scope ID)
MAIL_READ_ID="570282fd-fa5c-430d-a7fd-fc8dc98a9b53"
# Mail.Send (scope ID)
MAIL_SEND_ID="024d486e-b451-40bb-833d-3b569c03d37e"

# Add permissions (note: requires tenant admin consent, but this requests them)
az ad app permission add \
  --id "$CLIENT_ID" \
  --api "$GRAPH_ID" \
  --api-permissions "$MAIL_READ_ID=Scope" "$MAIL_SEND_ID=Scope" 2>/dev/null || echo "Permissions already added"

echo ""
echo "========================================="
echo "✅ Setup Complete!"
echo "========================================="
echo ""
echo "Your CLIENT_ID:"
echo "  $CLIENT_ID"
echo ""
echo "Add this to your .env file:"
echo "  EXTERNAL_OUTLOOK_CLIENT_ID=$CLIENT_ID"
echo ""
echo "Redirect URI registered:"
echo "  $REDIRECT_URI"
echo ""
echo "Next steps:"
echo "  1. Set env var: EXTERNAL_OUTLOOK_CLIENT_ID=$CLIENT_ID"
echo "  2. (Optional) Grant admin consent in Azure Portal"
echo "  3. Update frontend redirect URI if different"
echo ""
echo "To test:"
echo "  curl http://localhost:8000/auth/external-outlook-config"
echo ""
