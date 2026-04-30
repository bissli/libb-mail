libb-mail
========

A small mail-sending library with two interchangeable backends: Mandrill
(Mailchimp Transactional) and AWS SES. Pick one per call or globally.

INSTALL
-------

Mandrill only (default backend):

    pip install libb-mail

To enable the AWS SES backend, install the `ses` extra:

    pip install libb-mail[ses]

The extra pulls in `boto3`. If `provider='ses'` is selected without the
extra installed, `send_mail` raises `ImportError` with the install hint.

USAGE
-----

    from mail import send_mail

    result = send_mail(
        'jmilton@example.com',          # sender (4-arg form)
        ['recipient@example.com'],      # recipients (list)
        'subj',                         # subject
        'body',                         # body
        priority='High',                # 'High' | 'Normal' (default) | 'Low'
        subtype='plain',                # 'plain' (default) | 'html'
        cclist=['cc@example.com'],
        bcclist=['bcc@example.com'],
        attachments=[{'data': b'...', 'name': 'doc.pdf',
                      'maintype': 'application', 'subtype': 'pdf'}],
        inline_images=[{'data': b'...', 'maintype': 'image',
                        'subtype': 'png', 'name': 'logo',
                        'cid': 'logo'}],
        provider='ses',                 # overrides CONFIG_MAIL_PROVIDER
    )

The 3-arg form omits the sender (it defaults to `CONFIG_MAIL_FROMEMAIL`):

    send_mail(['recipient@example.com'], 'subj', 'body')

`send_mail` returns a result dict:

    # Mandrill
    {'provider': 'mandrill',
     'message_id': '...',
     'recipients': [{'email': '...', 'status': 'sent', 'reject_reason': None}, ...]}

    # SES
    {'provider': 'ses', 'message_id': '...'}

It raises on failure (no silent swallow):

- `mailchimp_transactional.api_client.ApiClientError` — Mandrill HTTP error.
- `MailSendError` — Mandrill rejected every recipient.
- `botocore.exceptions.ClientError` — SES error (e.g., `MessageRejected`).
- `ImportError` — `provider='ses'` selected without the `ses` extra.
- `ValueError` — unknown provider.

BACKEND SELECTION
-----------------

Two ways to pick the backend:

1. Global default via env var `CONFIG_MAIL_PROVIDER` (`mandrill` or `ses`,
   default `mandrill`).
2. Per-call override via `provider=` kwarg, which beats the env var.

ENVIRONMENT VARIABLES
---------------------

### General

| Variable                 | Purpose                                               | Default    |
| ------------------------ | ----------------------------------------------------- | ---------- |
| `CONFIG_MAIL_DOMAIN`     | Domain appended to bare usernames in recipient lists. | (unset)    |
| `CONFIG_MAIL_SERVER`     | IMAP server (used by the IMAP receiver, not senders). | (unset)    |
| `CONFIG_MAIL_FROMEMAIL`  | Default sender used by the 3-arg `send_mail` form.    | (unset)    |
| `CONFIG_MAIL_TOEMAIL`    | Convenience recipient (used by tests / scripts).      | (unset)    |
| `CONFIG_MAIL_ADMINEMAIL` | Convenience admin recipient.                          | (unset)    |
| `CONFIG_MAIL_PROVIDER`   | Active backend: `mandrill` or `ses`.                  | `mandrill` |

### Mandrill

| Variable                 | Purpose                                            | Default                            |
| ------------------------ | -------------------------------------------------- | ---------------------------------- |
| `CONFIG_MANDRILL_APIKEY` | Mandrill API key. Required when provider=mandrill. | (unset)                            |
| `CONFIG_MANDRILL_SMTP`   | Mandrill SMTP host (informational).                | `smtp.mandrillapp.com`             |
| `CONFIG_MANDRILL_URL`    | Mandrill REST endpoint.                            | `https://mandrillapp.com/api/1.0/` |

### AWS SES (only used when provider=ses)

| Variable                       | Purpose                           | Default     |
| ------------------------------ | --------------------------------- | ----------- |
| `CONFIG_SES_REGION`            | AWS region for SES.               | `us-east-1` |
| `CONFIG_SES_ACCESS_KEY_ID`     | Static AWS access key (optional). | (unset)     |
| `CONFIG_SES_SECRET_ACCESS_KEY` | Static AWS secret key (optional). | (unset)     |

When the static keys are unset, boto3 uses its default credential chain
(`~/.aws/credentials`, IAM instance role, ECS task role, environment
variables). In production prefer instance / task role over static keys.

The verified sender identity (the `From:` address) and DKIM CNAMEs must
be configured in SES separately; this library does not provision them.

BCC, CC, ATTACHMENTS, INLINE IMAGES
-----------------------------------

- `cclist` / `bcclist` are passed via the SES `Destinations` envelope and,
  for cc, also written into the `Cc:` MIME header. **BCC addresses are
  never written to MIME headers** (which would leak them to all recipients
  since SES forwards raw MIME unchanged).
- `attachments` accepts either path strings or dicts with keys
  `path`, `data`, `name`, `maintype`, `subtype`.
- `inline_images` accepts dicts with keys `data`, `maintype`, `subtype`,
  `name`, and (for SES) `cid`. The HTML body should reference each image
  as `cid:<cid>`. Mandrill bodies use the legacy `*|name|*` merge-tag
  scheme; HTML bodies need to be rewritten to `cid:` form when migrating
  a caller to SES.

KNOWN LIMITATIONS
-----------------

- Suppression / unsubscribe / hard-bounce state is **not** synced between
  Mandrill and SES. A recipient who unsubscribed via Mandrill will still
  receive mail through the SES backend unless suppressed there too.
- Each `send_mail` call uses one backend end-to-end. There is no failover
  on transport errors; the exception propagates so callers can decide.

DEVELOPMENT
-----------

Tests run fully offline (Mandrill and boto3 are mocked):

    pip install libb-mail[test]
    pytest tests/
