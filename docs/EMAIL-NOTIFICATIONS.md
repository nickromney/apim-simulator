# Local email notifications

The simulator models the Azure API Management notification workflow without
sending real email. Configure recipients and templates in the gateway config,
run a portal subscription workflow, and read the rendered messages from the
local outbox.

This is useful for the APIM hands-on-lab step “customise email notifications”:
the expected result is a message in the outbox, not delivery to an inbox.

## Prerequisites

Start a management-enabled stack first:

```bash
make up
```

The example stack uses `local-dev-tenant-key`. Keep the tenant key in the
`X-Apim-Tenant-Key` header for every management request.

## Configure recipients and settings

Add a publisher recipient with the Azure-shaped notification name:

```bash
curl -X PUT \
  -H 'X-Apim-Tenant-Key: local-dev-tenant-key' \
  http://localhost:8000/apim/management/notifications/RequestPublisherNotificationMessage/recipient-emails/publisher@example.test
```

Configure the BCC recipient and general email settings:

```bash
curl -X PUT \
  -H 'X-Apim-Tenant-Key: local-dev-tenant-key' \
  -H 'Content-Type: application/json' \
  -d '{"administrator_email":"admin@example.test","organization_name":"Example APIs","originating_email":"no-reply@example.test"}' \
  http://localhost:8000/apim/management/email-settings

curl -X POST \
  -H 'X-Apim-Tenant-Key: local-dev-tenant-key' \
  -H 'Content-Type: application/json' \
  -d '{"email":"audit@example.test"}' \
  http://localhost:8000/apim/management/notifications/BCC/recipient-emails
```

List configured notifications and recipients:

```bash
curl -H 'X-Apim-Tenant-Key: local-dev-tenant-key' \
  http://localhost:8000/apim/management/notifications
```

## Customize and reset a template

Templates have a plain-text subject and an HTML body. Supported values include
`$DevFirstName`, `$DevLastName`, `$DevEmail`, `$OrganizationName`,
`$DevPortalUrl`, `$ProdName`, and `$SubId`. An unknown `$Parameter` remains
literal.

```bash
curl -X PUT \
  -H 'X-Apim-Tenant-Key: local-dev-tenant-key' \
  -H 'Content-Type: application/json' \
  -d '{"subject":"Welcome, $DevFirstName","body":"<p>Your $ProdName subscription is ready.</p>"}' \
  http://localhost:8000/apim/management/email-templates/PurchaseDeveloperNotificationMessage
```

The template response reports `"customized": true`. Delete it to restore the
built-in default, matching Azure’s DELETE reset behavior:

```bash
curl -X DELETE \
  -H 'X-Apim-Tenant-Key: local-dev-tenant-key' \
  http://localhost:8000/apim/management/email-templates/PurchaseDeveloperNotificationMessage
```

## Exercise the portal flow

Use the developer portal at
<http://localhost:8000/apim/portal>. Request a product subscription. The
result is:

| Flow | Outbox notifications |
| --- | --- |
| Approval-required product request | `RequestPublisherNotificationMessage` and `RequestDeveloperNotificationMessage` |
| Submitted subscription approved through management | `PurchasePublisherNotificationMessage` and `PurchaseDeveloperNotificationMessage` |
| Submitted subscription rejected through management | `RejectDeveloperNotificationMessage` |
| Immediately active subscription | `PurchasePublisherNotificationMessage` and `PurchaseDeveloperNotificationMessage` when publisher recipients are configured |

Inspect messages newest first:

```bash
curl -H 'X-Apim-Tenant-Key: local-dev-tenant-key' \
  http://localhost:8000/apim/management/outbox
```

The outbox keeps the newest 200 messages in memory. Clear it between lab runs:

```bash
curl -X DELETE \
  -H 'X-Apim-Tenant-Key: local-dev-tenant-key' \
  http://localhost:8000/apim/management/outbox
```

Developer addresses come from the matching configured user record. BCC
recipients are added only to developer messages. Subscription keys are never
included in a notification subject or body.

## Management endpoints

All endpoints below require the tenant key.

| Method | Endpoint | Result |
| --- | --- | --- |
| GET | `/apim/management/notifications` | List Azure-shaped publisher notification names and recipients |
| GET | `/apim/management/notifications/{name}` | Get one notification |
| GET | `/apim/management/notifications/{name}/recipient-emails` | List direct email recipients |
| PUT/POST | `/apim/management/notifications/{name}/recipient-emails[/{email}]` | Add a direct recipient |
| DELETE | `/apim/management/notifications/{name}/recipient-emails/{email}` | Remove a direct recipient |
| GET | `/apim/management/email-templates` | List effective templates |
| GET/PUT/DELETE | `/apim/management/email-templates/{name}` | Get, customize, or reset a template |
| GET/PUT | `/apim/management/email-settings` | Get or update administrator/from/organization settings |
| GET/DELETE | `/apim/management/outbox` | Read newest-first messages or clear the outbox |

## How this differs from Azure

| Azure API Management | Local simulator |
| --- | --- |
| Sends email through the APIM service | Does not use SMTP; it records messages in a local outbox |
| Notification and template resources are service resources | Recipients, overrides, and settings are config-backed and management-editable |
| Built-in templates support documented parameters | The listed APIM-shaped templates support local substitution; unknown parameters stay literal |
| Email delivery has Azure subscription and sender-domain considerations | `originating_email` is metadata only; no delivery, SPF, or DKIM is attempted |
| Notifications cover the full APIM service lifecycle | Wired locally: portal request, subscription activation/approval, rejection, publisher recipients, developer mail, and BCC |
| Azure can retain service configuration | The outbox is process memory and is lost when the gateway restarts; config edits are persisted only when `APIM_CONFIG_PATH` is set |

The resource names and reset behavior are based on Microsoft’s
[notification](https://learn.microsoft.com/en-us/rest/api/apimanagement/notification),
[recipient email](https://learn.microsoft.com/en-us/rest/api/apimanagement/notification-recipient-email),
and [email template](https://learn.microsoft.com/en-us/rest/api/apimanagement/email-template)
references.
