# retrolocation

Exposes the location of your most recently shared Retro (https://retro.app/) photos through Sofia's handy [retro-sdk](https://github.com/eeriergosling/retro-sdk).

# Usage

Deploy using the provided Dockerfile. 

```
GET /location
X-Query-Secret: <RETROLOCATION_QUERY_SECRET>
```
Returns the location of your latest Retro post (including posts you reshared). Requests without the `X-Query-Secret` header get a 403.

```
GET /location?count=x&weeks=y[&spread=true]
X-Query-Secret: <RETROLOCATION_QUERY_SECRET>
```
Returns up to `x` distinct locations from the past `y` weeks, newest first. If there are fewer than `x` distinct locations, it returns all of them. `count` must be 1–50 and `weeks` 1–12. Requests without the `X-Query-Secret` header get a 403.

With `spread=true`, the window is split into `x` equal time slots and one location is picked per slot, preferring locations that are distinct (so favouring locations in different cities, for instance).

# Environment variables

| variable | required | default | description |
| --- | --- | --- | --- |
| `RETROLOCATION_ADMIN_SECRET` | yes | none | secret sent in the `x-admin-secret` header on `/auth/*`. must be at least 32 characters. |
| `RETROLOCATION_QUERY_SECRET` | yes | none | secret sent in the `x-query-secret` header to use `/location`. must be at least 32 characters. |
| `RETROLOCATION_CORS_ORIGINS` | no | none | comma-separated origins allowed to call `/location` from a browser, e.g. `https://phthallo.com`. |
| `RETRO_USER_ID` | yes | none | the retro user id of the only account allowed to sign in. see the next subheading |
| `RETRO_TOKEN_KEY` | yes | none | fernet key used to encrypt the saved refresh token. the server won't start without a valid key. . generate with `uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `RETRO_TOKEN_FILE` | no | `.retro_refresh_token` (docker: `/data/retro_refresh_token`) | where the encrypted refresh token is saved. |
| `HOST` | no | `127.0.0.1` | address to bind |
| `PORT` | no | `8000` | port to listen on. |

## Retro user ID

Get your internal Retro user ID by running this command:

```
uv run python -c "from retro_sdk import Retro; print(Retro().get_user_id('<YOURRETROUSERNAME>'))"
```