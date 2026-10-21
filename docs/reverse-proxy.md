# Behind a reverse proxy

HexDeck speaks plain HTTP on port 8000. In front of it a reverse proxy adds
https, which browsers need for Web Push and for installing HexDeck as an
app, and which sign-in providers usually insist on.

What every proxy has to get right:

- **No buffering on the live updates.** The boards are kept current over
  Server-Sent Events (`/api/v1/stream`, and the container logs at
  `/api/v1/widgets/<id>/logs/stream`). A proxy that buffers holds the events
  back, and the cards stop moving until a reload. Traefik and Caddy pass them
  as they come; nginx needs `proxy_buffering off`.
- **Big enough uploads.** An ordinary request may carry 16 MB (a background
  picture); restoring a backup up to 512 MB. nginx refuses anything over 1 MB
  unless told otherwise.
- **The scheme passed on.** HexDeck sets its cookies `Secure` when the request
  came in over https, which it learns from `X-Forwarded-Proto`. All three
  proxies below send it.
- **Who is asking.** For the count of failed sign-ins, the sign-in on trusted
  networks and the action log, HexDeck reads the client's address from
  `X-Forwarded-For`, but only from proxies named in `HEXDECK_TRUSTED_PROXIES`.
  Name the proxy's address or network there, for example
  `HEXDECK_TRUSTED_PROXIES=172.18.0.0/16` for a proxy on the same Docker
  network.
- **The public address.** Set `HEXDECK_PUBLIC_URL=https://deck.example.com`
  (or **System → Address**): sign-in providers, password reset mails and
  Web Push links are built from it.

## nginx

```nginx
server {
    listen 443 ssl;
    http2 on;
    server_name deck.example.com;
    # ssl_certificate and ssl_certificate_key as usual

    client_max_body_size 512m;

    location / {
        proxy_pass http://hexdeck:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # Server-Sent Events: pass each event on at once, and let the
        # connection stay open longer than nginx's default minute.
        proxy_buffering off;
        proxy_read_timeout 1h;
    }
}
```

Nginx Proxy Manager: the same in the proxy host's **Advanced** tab, as
`client_max_body_size 512m;`, `proxy_buffering off;` and
`proxy_read_timeout 1h;`.

## Traefik

Labels on the HexDeck service, with an entry point `websecure` and a
certificate resolver `le` already set up in Traefik:

```yaml
    labels:
      - traefik.enable=true
      - traefik.http.routers.hexdeck.rule=Host(`deck.example.com`)
      - traefik.http.routers.hexdeck.entrypoints=websecure
      - traefik.http.routers.hexdeck.tls.certresolver=le
      - traefik.http.services.hexdeck.loadbalancer.server.port=8000
```

Traefik does not buffer responses and sets the forwarded headers itself. It
has no default limit on request bodies.

## Caddy

```
deck.example.com {
    reverse_proxy hexdeck:8000
}
```

Caddy gets the certificate, streams events as they come and sets the
forwarded headers. Nothing else is needed.

## A path instead of a host

HexDeck expects to sit at the root of its address (`https://deck.example.com/`).
Under a path such as `https://example.com/deck/` its links and its service
worker point at the wrong place; give it a host name of its own.

## Checking it

- The cards change by themselves without a reload: the events get through.
- **System → Address** shows the public address HexDeck uses.
- A sign-in from another device is logged in **System → Log** as "signed in
  from" that device's address, not the proxy's: `HEXDECK_TRUSTED_PROXIES` is
  right.
