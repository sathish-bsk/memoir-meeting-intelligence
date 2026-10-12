# Memoir — Integration Setup Guide (Production)

Step-by-step configuration for every external integration. All redirect URIs use `https://app.meetmemoir.com` as the base.

---

## Table of Contents

1. [Google (Login + Calendar)](#1-google-login--calendar)
2. [Microsoft Azure (Login + Outlook Calendar + Teams Cloud Recording)](#2-microsoft-azure-login--outlook-calendar--teams-cloud-recording)
3. [Zoom (Cloud Recording)](#3-zoom-cloud-recording)
4. [Slack (Notifications)](#4-slack-notifications)
5. [HubSpot (CRM)](#5-hubspot-crm)
6. [Zoho CRM](#6-zoho-crm)
7. [Calendar Webhooks (Google + Microsoft push notifications)](#7-calendar-webhooks-google--microsoft-push-notifications)
8. [Encryption Keys](#8-encryption-keys)
9. [DNS & SSL](#9-dns--ssl)
10. [Quick Checklist](#10-quick-checklist)
11. [Environment Variables Reference](#11-environment-variables-reference)

---

## 1. Google (Login + Calendar)

**Console**: [console.cloud.google.com](https://console.cloud.google.com)

### Step 1: Create OAuth Credentials

1. Go to **APIs & Services → Credentials**
2. Click **Create Credentials → OAuth 2.0 Client ID**
3. Application type: **Web application**
4. Name: "Memoir"
5. Add **Authorized redirect URIs**:
   - `https://app.meetmemoir.com/oauth/callback/google` (for login)
   - `https://app.meetmemoir.com/integrations` (for calendar connection)
6. Click **Create**
7. Copy the **Client ID** and **Client Secret**

### Step 2: Enable APIs

1. Go to **APIs & Services → Library**
2. Search and enable:
   - **Google Calendar API**

### Step 3: Domain Verification (required for Calendar push notifications)

1. Go to [Google Search Console](https://search.google.com/search-console)
2. Click **Add property**
3. Choose **URL prefix** → enter `https://app.meetmemoir.com`
4. Verify ownership via **DNS TXT record**:
   - Add a TXT record to your DNS: `google-site-verification=<token>`
   - Wait for propagation and click **Verify**

Without domain verification, Google will reject Calendar push notification channel registration (`events.watch()` will return 401).

### Step 4: OAuth Consent Screen

1. Go to **APIs & Services → OAuth consent screen**
2. User type: **External** (or Internal if Google Workspace only)
3. Fill in app name, support email, authorized domains
4. Add scopes:
   - `openid`
   - `email`
   - `profile`
   - `https://www.googleapis.com/auth/calendar.events.readonly`
   - `https://www.googleapis.com/auth/userinfo.email`
5. Add test users (if in testing mode, only these users can authorize)
6. Submit for verification when ready to go public

### Environment Variables

```bash
# auth_service/.env.production
OAUTH_GOOGLE_CLIENT_ID=<client-id>
OAUTH_GOOGLE_CLIENT_SECRET=<client-secret>
OAUTH_GOOGLE_REDIRECT_URI=https://app.meetmemoir.com/oauth/callback/google

# integration_service/.env.production
GOOGLE_CLIENT_ID=<client-id>
GOOGLE_CLIENT_SECRET=<client-secret>

# core_api/.env.production
GOOGLE_CLIENT_ID=<client-id>
GOOGLE_CLIENT_SECRET=<client-secret>
```

> **Note**: Login and Calendar use the same Google Cloud project but different OAuth scopes. The Client ID and Secret are the same across auth_service, integration_service, and core_api.

---

## 2. Microsoft Azure (Login + Outlook Calendar + Teams Cloud Recording)

**Console**: [portal.azure.com](https://portal.azure.com)

A single Azure App Registration is shared across login, Outlook Calendar, and Teams Cloud Recording.

### Step 1: Create App Registration

1. Go to **Azure Active Directory → App registrations → New registration**
2. Name: "Memoir"
3. Supported account types: **Accounts in any organizational directory (Multitenant)**
4. Click **Register**
5. Copy the **Application (client) ID**

### Step 2: Add Client Secret

1. Go to **Certificates & secrets → New client secret**
2. Description: "Memoir Production"
3. Expiry: 24 months (set a calendar reminder to rotate)
4. Click **Add**
5. Copy the **Value** immediately (it won't be shown again)

### Step 3: Configure Redirect URIs

1. Go to **Authentication → Platform configurations → Add a platform → Web**
2. Add redirect URIs:
   - `https://app.meetmemoir.com/oauth/callback/microsoft` (for login)
   - `https://app.meetmemoir.com/integrations` (for calendar + Teams connection)
3. Click **Configure**

### Step 4: Add API Permissions

1. Go to **API permissions → Add a permission → Microsoft Graph**

2. Add **Delegated permissions**:
   - `openid` (login)
   - `email` (login)
   - `profile` (login)
   - `User.Read` (login + calendar + Teams)
   - `Calendars.Read` (Outlook Calendar)
   - `offline_access` (refresh tokens)
   - `OnlineMeetings.Read` (Teams Cloud)

3. Add **Application permissions** (for Teams Cloud Recording):
   - `CallRecords.Read.All` (receive call record webhooks)
   - `OnlineMeetings.Read.All` (download recordings)

4. Click **Grant admin consent for [your organization]**

> **Important**: Application permissions require **admin consent**. If Memoir is used by external organizations, each organization's admin must grant consent separately. The app handles this via a dedicated admin consent flow in the UI.

### Step 5: Generate Client State Secret

Generate a random secret for webhook payload verification:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Environment Variables

```bash
# auth_service/.env.production
OAUTH_MICROSOFT_CLIENT_ID=<application-client-id>
OAUTH_MICROSOFT_CLIENT_SECRET=<client-secret-value>

# integration_service/.env.production
OUTLOOK_CLIENT_ID=<application-client-id>
OUTLOOK_CLIENT_SECRET=<client-secret-value>
TEAMS_CLOUD_CLIENT_STATE_SECRET=<random-secret>

# core_api/.env.production
OUTLOOK_CLIENT_ID=<application-client-id>
OUTLOOK_CLIENT_SECRET=<client-secret-value>
TEAMS_CLOUD_WEBHOOK_URL=https://app.meetmemoir.com/api/teams-cloud/webhooks/call-records
TEAMS_CLOUD_CLIENT_STATE_SECRET=<same-random-secret>
```

### Teams Cloud Recording: Admin Consent Flow

When a user connects Teams Cloud Recording:
1. User authenticates with delegated permissions (User.Read, OnlineMeetings.Read)
2. Frontend displays an "Admin Consent Required" prompt
3. The organization's Azure AD admin opens the consent URL:
   ```
   https://login.microsoftonline.com/{tenant_id}/adminconsent?client_id={CLIENT_ID}&redirect_uri=https://app.meetmemoir.com/integrations
   ```
4. Admin grants CallRecords.Read.All and OnlineMeetings.Read.All
5. Memoir creates a Graph subscription for `communications/callRecords`
6. Subscription auto-renews every ~71 hours

---

## 3. Zoom (Cloud Recording)

**Console**: [marketplace.zoom.us](https://marketplace.zoom.us)

### Step 1: Create OAuth App

1. Go to **Develop → Build App**
2. Choose **OAuth** app type → Create
3. App name: "Memoir"
4. App type: **Account-level app**

### Step 2: Configure OAuth

1. Go to the app's **App Credentials** tab
2. Add **Redirect URL**: `https://app.meetmemoir.com/integrations`
3. Add **Allow List URL**: `https://app.meetmemoir.com`
4. Copy **Client ID** and **Client Secret**

### Step 3: Configure Webhook

1. Go to **Feature → Event Subscriptions**
2. Toggle **Event Subscriptions** on
3. Click **Add Event Subscription**
4. Subscription name: "Recording Complete"
5. Event notification endpoint URL: `https://app.meetmemoir.com/api/zoom/webhooks`
6. Add event type: **recording.completed** (under Recording)
7. Click **Save**
8. Copy the **Secret Token** (used for HMAC-SHA256 signature verification)

### Step 4: App Activation

1. Go to **Activation** tab
2. Add required information (company name, description, etc.)
3. For internal use: Activate immediately
4. For marketplace listing: Submit for review

### Environment Variables

```bash
# integration_service/.env.production
ZOOM_CLIENT_ID=<client-id>
ZOOM_CLIENT_SECRET=<client-secret>
ZOOM_ENCRYPTION_KEY=<generate-fernet-key>

# core_api/.env.production
ZOOM_CLIENT_ID=<client-id>
ZOOM_CLIENT_SECRET=<client-secret>
ZOOM_WEBHOOK_SECRET_TOKEN=<secret-token-from-webhook-config>
ZOOM_ENCRYPTION_KEY=<same-fernet-key>
```

### Webhook Verification

Zoom verifies your webhook endpoint on setup by sending an `endpoint.url_validation` event. Memoir handles this automatically — it decrypts the `plainToken` using `ZOOM_WEBHOOK_SECRET_TOKEN` and returns the `encryptedToken`.

For subsequent `recording.completed` events, Memoir verifies the HMAC-SHA256 signature:
```
signature = v0={HMAC_SHA256("v0:{timestamp}:{body}", ZOOM_WEBHOOK_SECRET_TOKEN)}
```

---

## 4. Slack (Notifications)

**Console**: [api.slack.com/apps](https://api.slack.com/apps)

Memoir uses Slack for **outbound notifications only** (no incoming events/commands).

### Step 1: Create Slack App

1. Click **Create New App → From scratch**
2. App name: "Memoir"
3. Pick a development workspace
4. Click **Create App**

### Step 2: Configure Bot Scopes

1. Go to **OAuth & Permissions**
2. Under **Bot Token Scopes**, add:
   - `chat:write` — Send messages to channels
   - `channels:join` — Auto-join public channels (so bot can post)
   - `channels:read` — List public channels (for channel picker in UI)
   - `groups:read` — List private channels the bot has been added to

### Step 3: Add Redirect URL

1. Under **OAuth & Permissions → Redirect URLs**
2. Add: `https://app.meetmemoir.com/integrations`
3. Click **Save URLs**

### Step 4: Copy Credentials

1. Go to **Basic Information**
2. Copy:
   - **Client ID** (under App Credentials)
   - **Client Secret** (under App Credentials)
   - **Signing Secret** (under App Credentials)

### Step 5: Install to Workspace (Optional, for testing)

1. Go to **OAuth & Permissions**
2. Click **Install to Workspace**
3. Authorize the bot

### Step 6: Distribution (for multi-workspace)

1. Go to **Manage Distribution**
2. Enable distribution if you want other Slack workspaces to install Memoir
3. Complete the checklist (app description, icons, etc.)

### Environment Variables

```bash
# integration_service/.env.production
SLACK_CLIENT_ID=<client-id>
SLACK_CLIENT_SECRET=<client-secret>
SLACK_SIGNING_SECRET=<signing-secret>
SLACK_ENCRYPTION_KEY=<generate-fernet-key>
SLACK_OAUTH_REDIRECT_URI=https://app.meetmemoir.com/integrations
```

### What Memoir does with Slack

- **Channel listing**: Fetches public + private channels for the channel picker in notification settings
- **Auto-join**: When a user selects a public channel, the bot auto-joins it
- **Message posting**: Sends notifications (call processed, digest ready) to the selected channel
- **Test message**: Users can send a test message to verify the integration

No event subscriptions, slash commands, or interactive components are used.

---

## 5. HubSpot (CRM)

**Console**: [developers.hubspot.com](https://developers.hubspot.com)

### Step 1: Create Developer Account

1. Sign up at [developers.hubspot.com](https://developers.hubspot.com)
2. Create a developer account (free)

### Step 2: Create App

1. Go to **Apps → Create app**
2. App name: "Memoir"
3. Go to the **Auth** tab

### Step 3: Configure OAuth

1. Add **Redirect URL**: `https://app.meetmemoir.com/integrations`
2. Add **Required Scopes**:
   - `crm.objects.contacts.read`
   - `crm.objects.contacts.write`
   - `crm.objects.deals.read`
   - `crm.objects.deals.write`
   - `crm.objects.companies.read`
   - `crm.objects.companies.write`
   - `oauth`
3. Copy **App ID** (Client ID) and **Client Secret**

### Step 4: App Listing (Optional)

For HubSpot Marketplace listing, complete the listing details and submit for review. Not required for internal use.

### Environment Variables

```bash
# integration_service/.env.production
HUBSPOT_CLIENT_ID=<app-id>
HUBSPOT_CLIENT_SECRET=<client-secret>
CRM_ENCRYPTION_KEY=<generate-fernet-key>
```

### What Memoir pushes to HubSpot

- **Contacts**: Creates or updates contacts using email as the unique identifier
- **Deals**: Creates deals linked to contacts, with mapped fields from call intelligence
- **Properties**: Reads contact and deal properties for field mapping configuration
- **Pipelines**: Reads deal pipelines and stages for stage mapping

---

## 6. Zoho CRM

**Console**: [api-console.zoho.com](https://api-console.zoho.com)

### Step 1: Create Server-based Application

1. Go to [api-console.zoho.com](https://api-console.zoho.com)
2. Click **Add Client → Server-based Applications**
3. Client name: "Memoir"
4. Homepage URL: `https://app.meetmemoir.com`
5. Authorized redirect URI: `https://app.meetmemoir.com/integrations`
6. Click **Create**
7. Copy **Client ID** and **Client Secret**

### Step 2: Scopes

Memoir requests these scopes during the OAuth flow:
- `ZohoCRM.modules.ALL` — Read/write CRM modules (contacts, deals)
- `ZohoCRM.settings.ALL` — Read CRM settings (fields, pipelines)
- `ZohoCRM.org.READ` — Read organization info
- `ZohoCRM.users.READ` — Read user info

### Multi-Region Support

Zoho has separate data centers. The app works across all regions automatically — the OAuth response includes the correct `api_domain` and `accounts_url` which Memoir stores per connection.

| Region | Accounts URL | API Domain |
|--------|-------------|------------|
| US | accounts.zoho.com | zohoapis.com |
| EU | accounts.zoho.eu | zohoapis.eu |
| IN | accounts.zoho.in | zohoapis.in |
| AU | accounts.zoho.com.au | zohoapis.com.au |
| JP | accounts.zoho.jp | zohoapis.jp |
| CA | accounts.zohocloud.ca | zohoapis.ca |

If you have users across multiple regions, the same Client ID/Secret works everywhere — no separate registration needed per region.

### Environment Variables

```bash
# integration_service/.env.production
ZOHO_CLIENT_ID=<client-id>
ZOHO_CLIENT_SECRET=<client-secret>
CRM_ENCRYPTION_KEY=<same-fernet-key-as-hubspot>
```

### What Memoir pushes to Zoho

- **Contacts**: Upserts contacts using email as the unique identifier
- **Deals**: Creates/updates deals linked to contacts
- **Properties**: Reads module fields for field mapping
- **Stages**: Reads deal pipelines for stage mapping
- **Auth header**: Uses `Zoho-oauthtoken` (not Bearer) per Zoho API requirements

---

## 7. Calendar Webhooks (Google + Microsoft push notifications)

No external configuration needed — channels/subscriptions are registered programmatically when users connect their calendars.

### How it works

**Google**: When a user connects Google Calendar, Memoir calls `events.watch()` to register a push notification channel. Google sends a POST to your webhook URL whenever any event changes.

**Microsoft**: When a user connects Outlook Calendar, Memoir creates a Graph subscription via `POST /subscriptions`. Microsoft validates the endpoint by sending a `validationToken` that must be echoed back.

### Requirements

- Webhook endpoint must be **publicly accessible HTTPS**
- Google requires **domain verification** (see Section 1, Step 3)
- Microsoft does **not** require domain verification

### Environment Variables

```bash
# core_api/.env.production
CALENDAR_WEBHOOK_BASE_URL=https://app.meetmemoir.com
CALENDAR_ENCRYPTION_KEY=<same-key-as-integration-service>

# integration_service/.env.production
CALENDAR_WEBHOOK_BASE_URL=https://app.meetmemoir.com
```

### Webhook Endpoints

| Provider | URL | Registered by |
|----------|-----|---------------|
| Google Calendar | `https://app.meetmemoir.com/api/webhooks/google-calendar` | Auto on connect |
| Microsoft Calendar | `https://app.meetmemoir.com/api/webhooks/microsoft-calendar` | Auto on connect |

### Renewal

- Google watch channels expire in ~7 days → auto-renewed every 6 hours
- Microsoft subscriptions expire in ~3 days → auto-renewed every 6 hours

---

## 8. Encryption Keys

Each integration group uses a separate Fernet encryption key to encrypt OAuth tokens at rest in the database.

### Generate a Fernet Key

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Output: 44-character base64 string (e.g., `ho9vcIoi4bfeoa3aZm5WPVaQN750siNW6bPZJMzfzSE=`)

### Key Assignment

| Key | Services | Protects |
|-----|----------|----------|
| `CALENDAR_ENCRYPTION_KEY` | integration_service, core_api | Google Calendar, Outlook Calendar, Teams Cloud OAuth tokens |
| `ZOOM_ENCRYPTION_KEY` | integration_service, core_api | Zoom OAuth tokens |
| `SLACK_ENCRYPTION_KEY` | integration_service | Slack bot tokens |
| `CRM_ENCRYPTION_KEY` | integration_service | HubSpot and Zoho OAuth tokens |
| `FERNET_KEY` | auth_service | Auth-level encrypted data |

Generate one key per row. Keys must match across services that share them (e.g., `CALENDAR_ENCRYPTION_KEY` must be the same in integration_service and core_api).

---

## 9. DNS & SSL

### DNS Record

Add an A record (or CNAME) for `app.meetmemoir.com` pointing to your EC2 instance's public IP.

```
app.meetmemoir.com  →  A  →  <EC2-PUBLIC-IP>
```

### SSL Certificate

Traefik auto-provisions SSL certificates via Let's Encrypt (ACME). No manual certificate management needed. The ACME email is configured in `api_gateway_service/docker-compose.yml`:

```yaml
ACME_EMAIL=hello@meetmemoir.com
```

Traefik will automatically:
1. Request a certificate from Let's Encrypt on first HTTPS request
2. Store it in the `acme-data` volume
3. Auto-renew before expiry

---

## 10. Quick Checklist

### External Platform Configuration

| Platform | Console | Redirect URIs | Webhook URL | Admin Consent |
|----------|---------|---------------|-------------|---------------|
| Google | console.cloud.google.com | `/oauth/callback/google`, `/integrations` | Auto-registered | No |
| Azure/Microsoft | portal.azure.com | `/oauth/callback/microsoft`, `/integrations` | `/api/teams-cloud/webhooks/call-records` | Yes (Teams Cloud) |
| Zoom | marketplace.zoom.us | `/integrations` | `/api/zoom/webhooks` | No |
| Slack | api.slack.com/apps | `/integrations` | None | No |
| HubSpot | developers.hubspot.com | `/integrations` | None | No |
| Zoho | api-console.zoho.com | `/integrations` | None | No |

### Pre-Deployment Verification

- [ ] DNS: `app.meetmemoir.com` resolves to EC2 IP
- [ ] SSL: HTTPS works on `app.meetmemoir.com`
- [ ] Google: Domain verified in Search Console
- [ ] Google: Calendar API enabled
- [ ] Azure: Admin consent granted for CallRecords.Read.All
- [ ] Zoom: Webhook subscription active for `recording.completed`
- [ ] All Fernet keys generated and matching across services
- [ ] All redirect URIs match `https://app.meetmemoir.com/...`
- [ ] `INTERNAL_SERVICE_KEY` matches across all services

---

## 11. Environment Variables Reference

### auth_service/.env.production

```bash
# Google OAuth (Login)
OAUTH_GOOGLE_CLIENT_ID=<google-client-id>
OAUTH_GOOGLE_CLIENT_SECRET=<google-client-secret>
OAUTH_GOOGLE_REDIRECT_URI=https://app.meetmemoir.com/oauth/callback/google

# Microsoft OAuth (Login)
OAUTH_MICROSOFT_CLIENT_ID=<azure-client-id>
OAUTH_MICROSOFT_CLIENT_SECRET=<azure-client-secret>

# Auth encryption
FERNET_KEY=<generate-fernet-key>

# Inter-service auth
INTERNAL_SERVICE_KEY=<shared-service-key>
```

### core_api/.env.production

```bash
# Frontend URL
FRONTEND_URL=https://app.meetmemoir.com

# Google Calendar (for push notification sync)
GOOGLE_CLIENT_ID=<google-client-id>
GOOGLE_CLIENT_SECRET=<google-client-secret>

# Microsoft (Teams Cloud + Calendar)
OUTLOOK_CLIENT_ID=<azure-client-id>
OUTLOOK_CLIENT_SECRET=<azure-client-secret>
TEAMS_CLOUD_WEBHOOK_URL=https://app.meetmemoir.com/api/teams-cloud/webhooks/call-records
TEAMS_CLOUD_CLIENT_STATE_SECRET=<random-secret>

# Calendar auto-join
CALENDAR_WEBHOOK_BASE_URL=https://app.meetmemoir.com
CALENDAR_ENCRYPTION_KEY=<fernet-key-shared-with-integration-service>

# Zoom
ZOOM_CLIENT_ID=<zoom-client-id>
ZOOM_CLIENT_SECRET=<zoom-client-secret>
ZOOM_WEBHOOK_SECRET_TOKEN=<zoom-webhook-secret>
ZOOM_ENCRYPTION_KEY=<fernet-key-shared-with-integration-service>

# Inter-service auth
INTERNAL_SERVICE_KEY=<shared-service-key>
```

### integration_service/.env.production

```bash
# Frontend URL
FRONTEND_URL=https://app.meetmemoir.com

# Google Calendar
GOOGLE_CLIENT_ID=<google-client-id>
GOOGLE_CLIENT_SECRET=<google-client-secret>
CALENDAR_ENCRYPTION_KEY=<generate-fernet-key>
CALENDAR_WEBHOOK_BASE_URL=https://app.meetmemoir.com

# Microsoft (Outlook Calendar + Teams Cloud)
OUTLOOK_CLIENT_ID=<azure-client-id>
OUTLOOK_CLIENT_SECRET=<azure-client-secret>
TEAMS_CLOUD_WEBHOOK_URL=https://app.meetmemoir.com/api/teams-cloud/webhooks/call-records
TEAMS_CLOUD_CLIENT_STATE_SECRET=<random-secret>

# Zoom
ZOOM_CLIENT_ID=<zoom-client-id>
ZOOM_CLIENT_SECRET=<zoom-client-secret>
ZOOM_ENCRYPTION_KEY=<generate-fernet-key>

# Slack
SLACK_CLIENT_ID=<slack-client-id>
SLACK_CLIENT_SECRET=<slack-client-secret>
SLACK_SIGNING_SECRET=<slack-signing-secret>
SLACK_ENCRYPTION_KEY=<generate-fernet-key>
SLACK_OAUTH_REDIRECT_URI=https://app.meetmemoir.com/integrations

# CRM (HubSpot + Zoho)
CRM_ENCRYPTION_KEY=<generate-fernet-key>
HUBSPOT_CLIENT_ID=<hubspot-app-id>
HUBSPOT_CLIENT_SECRET=<hubspot-client-secret>
ZOHO_CLIENT_ID=<zoho-client-id>
ZOHO_CLIENT_SECRET=<zoho-client-secret>

# Inter-service auth
INTERNAL_SERVICE_KEY=<shared-service-key>
```

---

*Last updated: March 2026*
