---
description: How to deploy the bot to Azure Web App for Containers
---

This workflow provides the commands to deploy the bot to Azure.

1. Login to Azure
```bash
az login
```

2. Create Resource Group
```bash
az group create --name BotResourceGroup --location eastus
```

3. Create Azure Container Registry (ACR)
// turbo
```bash
az acr create --resource-group BotResourceGroup --name mybotregistry${RANDOM} --sku Basic
```

4. Login to ACR
// turbo
```bash
az acr login --name $(az acr list --resource-group BotResourceGroup --query "[0].name" -o tsv)
```

5. Tag and Push Docker Image
// turbo
```bash
REGISTRY_NAME=$(az acr list --resource-group BotResourceGroup --query "[0].name" -o tsv)
docker tag national-id-bot ${REGISTRY_NAME}.azurecr.io/national-id-bot:latest
docker push ${REGISTRY_NAME}.azurecr.io/national-id-bot:latest
```

6. Create App Service Plan
// turbo
```bash
az appservice plan create --name BotServicePlan --resource-group BotResourceGroup --sku B1 --is-linux
```

7. Create Web App for Containers
// turbo
```bash
REGISTRY_NAME=$(az acr list --resource-group BotResourceGroup --query "[0].name" -o tsv)
az webapp create --resource-group BotResourceGroup --plan BotServicePlan --name my-id-bot-app${RANDOM} --deployment-container-image-name ${REGISTRY_NAME}.azurecr.io/national-id-bot:latest
```

8. Set Port and Environment Variables
// turbo
```bash
APP_NAME=$(az webapp list --resource-group BotResourceGroup --query "[0].name" -o tsv)
az webapp config appsettings set --resource-group BotResourceGroup --name ${APP_NAME} --settings WEBSITES_PORT=8000
```
