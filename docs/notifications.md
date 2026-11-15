# Notifications

Get pinged — Discord, [ntfy](https://ntfy.sh), [Apprise](https://github.com/caronc/apprise),
or any generic webhook receiver — when a subscription sync adds items, or when
a download started with `POST /entries/{id}/download` completes or fails.

Two events send a notification: `subscription_sync`, from
`media-archivist sync-subscriptions` and `POST /subscriptions/sync` when a run
adds at least one item, and `download_complete` or `download_failed`, from the
server's `POST /entries/{id}/download` task. Downloads queued through the
[App API](app-api.md) and the subscriptions the server indexes itself send
none; an app that needs those polls `GET /api/v1/downloads`.

Set `MEDIA_ARCHIVIST_WEBHOOK_URL` to your webhook URL. The shape of the POST
body is picked automatically from the URL:

- a Discord webhook URL (`.../api/webhooks/...`) gets `{"content": "..."}`
- an `ntfy.sh` URL (or setting `MEDIA_ARCHIVIST_NTFY_TOPIC`) gets ntfy's
  `{"message": ..., "title": ..., "topic": ...}` shape
- anything else gets a generic `{"event": ..., "message": ..., "data": {...}}`
  JSON body — point Apprise, n8n, or your own receiver at it

```shell
export MEDIA_ARCHIVIST_WEBHOOK_URL="https://discord.com/api/webhooks/XXX/YYY"
media-archivist notify-test   # send a test notification, verify delivery
media-archivist notify-test --message "custom test message"
```

A webhook failure (unreachable host, non-2xx response, timeout) is logged as
a warning and never breaks archiving, downloading, or syncing — notifications
are a side channel, not a dependency. No webhook configured is a silent
no-op.
