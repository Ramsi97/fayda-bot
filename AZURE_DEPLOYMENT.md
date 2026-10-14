# ☁️ Azure Deployment Guide (Student Credits)

This guide will help you deploy your Telegram Bot to Azure using **Web App for Containers**. This is the best method for your FastAPI bot.

## Prerequisites
1.  **Azure Student Account**: Make sure you have activated your credits at [azure.microsoft.com/free/students](https://azure.microsoft.com/free/students/).
2.  **Azure CLI**: Install it on your machine ([Instructions here](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli)).

---

## 🏗️ Step 1: Create Azure Resources
Open your terminal and run these commands:

1.  **Login to Azure**:
    ```bash
    az login
    ```

2.  **Create a Resource Group**:
    ```bash
    az group create --name BotResourceGroup --location eastus
    ```

3.  **Create an Azure Container Registry (ACR)**: 
    *(Choose a unique name instead of `mybotregistry`)*
    ```bash
    az acr create --resource-group BotResourceGroup --name mybotregistry --sku Basic
    ```

---

## 📤 Step 2: Build and Push your Docker Image

### Option A: Build in the Cloud (Recommended)
This is faster and avoids upload issues with large images. It builds directly in Azure.
1.  **Build in Azure**:
    *(Replace `nationald` with your registry name)*
    ```bash
    az acr build --registry nationald --image national-id-bot:latest .
    ```

### Option B: Build Locally and Push
1.  **Login to your Registry**:
    ```bash
    az acr login --name nationald
    ```

2.  **Build Locally**:
    ```bash
    docker build -t national-id-bot .
    ```

3.  **Tag your local image**:
    ```bash
    docker tag national-id-bot nationald.azurecr.io/national-id-bot:latest
    ```

4.  **Push the image**:
    ```bash
    docker push nationald.azurecr.io/national-id-bot:latest
    ```

---

## 🌐 Step 3: Create the Web App
1.  **Create an App Service Plan** (Free/B1 tier for students):
    ```bash
    az appservice plan create --name BotServicePlan --resource-group BotResourceGroup --sku B1 --is-linux
    ```

2.  **Create the Web App**:
    ```bash
    az webapp create --resource-group BotResourceGroup --plan BotServicePlan --name my-unique-bot-app --deployment-container-image-name mybotregistry.azurecr.io/national-id-bot:v1
    ```

---

## ⚙️ Step 4: Final Configuration
1.  **Enable Admin Access for ACR** (So the web app can pull the image):
    ```bash
    az acr update -n mybotregistry --admin-enabled true
    ```

2.  **Set Environment Variables**:
    Go to the **Azure Portal** -> Your Web App -> **Configuration** -> **New application setting**. Add the following:
    - `TELEGRAM_TOKEN`: (Your bot token)
    - `WEBHOOK_URL`: `https://my-unique-bot-app.azurewebsites.net`
    - `REQUIRED_GROUP_ID`: (Your group ID)
    - `AUTHORIZED_USER_IDS`: (Comma-separated admin Telegram IDs, e.g. `111,222`) — these admins approve deposits.
    - `PRICE_PER_DOWNLOAD`: Birr charged per generated ID card (e.g. `10`).
    - `FREE_SIGNUP_BALANCE`: Birr granted to each new user (default `0`).
    - `ADMIN_CHAT_ID`: (Optional) chat/group to receive deposit receipts. If unset, each `AUTHORIZED_USER_IDS` admin is DMed (they must have started the bot first).
    - `DATABASE_URL`: your Supabase Postgres connection string (**Session pooler**, port 5432), e.g. `postgresql://postgres.PROJECT_REF:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres` (see Step 4b).

3.  **Set the Port**:
    Add another setting: `WEBSITES_PORT` with value `8000`.

---

## 💾 Step 4b: Supabase Postgres for balances (REQUIRED)

User **balances and deposits live in Postgres**. The container filesystem is
**wiped on every restart/redeploy**, so balances must live in an external
database — not on disk. A managed Postgres is also safe if you ever scale to more
than one instance. The bot creates its own tables on first launch (no manual SQL
needed; see `schema.sql` for reference).

**Recommended: Supabase free tier.**
1. Create a free project at [supabase.com](https://supabase.com/dashboard) (pick a region close to your Azure region). Set a strong **database password** when prompted — save it.
2. No IP allowlist to manage — Supabase accepts connections from any IP by default, so the Azure Web App can connect out of the box.
3. **Project Settings → Database → Connection string → Session pooler** → copy the URI. It looks like:
   ```
   postgresql://postgres.PROJECT_REF:[YOUR-PASSWORD]@aws-0-REGION.pooler.supabase.com:5432/postgres
   ```
   Replace `[YOUR-PASSWORD]` with the password from step 1. URL-encode any special characters in the password (e.g. `@` → `%40`).
   > Use the **Session pooler** (IPv4, port 5432) for this long-lived bot — not the Transaction pooler (6543, for serverless) or the direct `db.<ref>.supabase.co` host (IPv6-only without a paid add-on). TLS is required and handled automatically.
4. In the Web App → **Configuration**, set:
   - `DATABASE_URL` = that connection string.

> No volume, `DB_PATH`, or `WEBSITES_ENABLE_APP_SERVICE_STORAGE` is needed anymore
> — those were only for the old SQLite file.

**Local development:** run Postgres locally (`docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres`)
and the default (`postgresql://postgres:postgres@localhost:5432/postgres`) works
out of the box (TLS is auto-disabled for localhost).

> ⚠️ Verify persistence after deploy: check a balance, restart the Web App, check
> again — it must be unchanged (the data lives in Supabase, not the container).

---

## 🔗 Step 5: Update Telegram Webhook
Visit this URL in your browser to tell Telegram to send updates to Azure:
`https://api.telegram.org/bot<YOUR_TOKEN>/setWebhook?url=https://my-unique-bot-app.azurewebsites.net/webhook`

---
✅ **Done!** Your bot is now running in the cloud.
