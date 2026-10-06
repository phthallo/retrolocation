# retrolocation

Exposes the location of your most recently shared Retro (https://retro.app/) photos through Sofia's handy [retro-sdk](https://github.com/eeriergosling/retro-sdk).

# Usage

Deploy using the provided Dockerfile. 

```
GET /public/recent
```
Public. Returns up to 3 spread-out locations from the past 4 weeks, the same as `/location?count=3&weeks=4&spread=true` with a ~24h delay. 

```
GET /location
X-Query-Secret: <RETROLOCATION_QUERY_SECRET>
```
Private. Returns the location of your latest Retro post (including posts you reshared).

```
GET /location?count=x&weeks=y[&spread=true]
X-Query-Secret: <RETROLOCATION_QUERY_SECRET>
```
Returns up to `x` distinct locations from the past `y` weeks, newest first. `count` must be 1–50 and `weeks` 1–12. 

With `spread=true`, it will prefer locations that are distinct (so favouring locations in different cities, for instance).

# Environment variables

| variable | required | default | description |
| --- | --- | --- | --- |
| `RETROLOCATION_ADMIN_SECRET` | yes | none | 32 character+ secret sent in the `x-admin-secret` header on `/auth/*` |
| `RETROLOCATION_QUERY_SECRET` | yes | none | 32 character+ secret sent in the `x-query-secret` header to use `/location`. |
| `RETROLOCATION_CORS_ORIGINS` | no | none | origins allowed to call `/location` and `/public/recent` from a browser|
| `RETRO_USER_ID` | yes | none | retro user id of authorised account, see the next subheading |
| `RETRO_TOKEN_KEY` | yes | none | fernet key used to encrypt the saved refresh token. |
| `RETRO_TOKEN_FILE` | no | `.retro_refresh_token` (docker: `/data/retro_refresh_token`) | where the encrypted refresh token is saved. |
| `HOST` | no | `127.0.0.1` | address to bind |
| `PORT` | no | `8000` | port to listen on. |

## Retro user ID

Get your internal Retro user ID by running this command:

```
uv run python -c "from retro_sdk import Retro; print(Retro().get_user_id('<YOURRETROUSERNAME>'))"
```