# Signing in: providers, home networks and second factors

Every account has a password unless it was made by a sign-in provider. On
top of that HexDeck knows three things, all under **System**: sign-in
providers (OpenID Connect), signing in by itself on trusted networks, and a
second factor per account.

## The public address comes first

A sign-in provider sends the browser back to HexDeck, so HexDeck has to know
the address people reach it under. Set it under **System → Address**, or with
`HEXDECK_PUBLIC_URL` (for example `https://deck.example.com`). Without it a
provider cannot be used, and the sign-in page says so.

The return address a provider has to allow is:

```
https://deck.example.com/api/v1/auth/oidc/<slug>/callback
```

where `<slug>` is the short name the provider gets in HexDeck (letters,
digits and hyphens, such as `authentik`).

## Sign-in providers (OpenID Connect)

**System → Sign-in providers → Add.** What it asks for:

| Field | What goes there |
|---|---|
| Slug | The short name in the return address above. |
| Label | The text on the sign-in button. |
| Issuer URL | The provider's issuer, the address its `/.well-known/openid-configuration` hangs off. For authentik: `https://auth.example.com/application/o/hexdeck/`. |
| Client ID, client secret | From the application made at the provider. |
| Scopes | `openid profile email` unless the provider wants more. |
| Create accounts | Off by default: only people whose account is already linked get in. On: anybody the provider lets through gets an account, with the role chosen beside it. |
| Default role | The role of an account made that way: user, guest or administrator. |
| The provider checks a second factor | On: an account with a second factor in HexDeck is not asked for its code again after this provider. Only tick it when the provider really asks for one. |

HexDeck uses the authorization code flow with PKCE, checks the issuer, the
audience and the nonce, and never links an identity to an account by its
e-mail address: an existing account is linked on purpose, from its own
**Settings → Profile** while signed in, with the provider's button there.
Changing a provider's issuer drops the links it had, because another issuer
is another provider.

### authentik in one go

For authentik there is a button under **System → Sign-in providers** that
does the dozen forms itself: give it authentik's address and a one-time API
token, and it creates the signing key, the scope mapping, the provider, the
application and HexDeck's own sign-in provider, step by step, saying which
step failed if one does. Pressing it again updates rather than duplicates.
The token is used for those calls only and never stored. Whoever would
rather not hand over a token downloads the blueprint beside the button and
imports it in authentik.

## Signing in by itself on trusted networks

Under **System → Sign-in providers** as well: name one or more networks
(`192.168.1.0/24`) and an account, and a browser from those networks is
signed in as that account without a password, until somebody signs out on
purpose.

- Such a browser may not create API tokens, set a password or link a sign-in
  provider: those would be ways in from anywhere.
- Behind a reverse proxy, the address HexDeck sees is the proxy's. Name the
  proxy in `HEXDECK_TRUSTED_PROXIES` and HexDeck reads the client's address
  from `X-Forwarded-For`, from that proxy only. Without it the header is
  ignored, because anybody could send one, and a whole proxy's worth of
  visitors would count as one trusted address or as none.
- Signing out on purpose holds: for a while that browser is not signed in
  again by itself, and the sign-in page offers the way back.

Pick an account with as few rights as the screen needs; a guest account is
usually enough for a wall display. For a display there is also the kiosk
link, see [Kiosk displays](kiosk.md).

## A second factor

Each person sets it up in **Settings → Profile**: an authenticator app
(TOTP) and a set of recovery codes, each good once. After the password the
sign-in asks for the code; wrong codes are counted and slowed like wrong
passwords. Switching it off asks for the password or a code again.

Keep the recovery codes somewhere else than the phone: they are the way back
for somebody who lost it. There is deliberately no switch for an
administrator to take another account's second factor off, and resetting a
password from the shell leaves the second factor as it is.

## When nobody can sign in

From the machine's shell, with the container running:

```
docker exec -it hexdeck python -m app.tools.reset_password admin
```

See [Getting back in](operating.md#getting-back-in).
