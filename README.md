# retrolocation

Exposes the location of your most recently shared Retro (https://retro.app/) photos through Sofia's handy [retro-sdk](https://github.com/eeriergosling/retro-sdk).

# Usage

Deploy using the provided Dockerfile. 

```
GET /location?count=x&weeks=y
```
Returns the location of your latest Retro post (including posts you reshared). `count` and `weeks` are optional parameters that will return `x` distinct locations from the past `y` weeks if included. If omitted, it will simply include the most recent location.


# Environment variables

| variable | required | default | description |
| --- | --- | --- | --- |
| `RETROLOCATION_ADMIN_SECRET` | yes | none | secret sent in the `x-admin-secret` header on `/auth/*`. must be at least 32 characters. |
| `RETRO_USER_ID` | yes | none | the retro user id of the only account allowed to sign in. see the next subheading |
| `RETRO_TOKEN_KEY` | yes | none | fernet key used to encrypt the saved refresh token. the server won't start without a valid key. changing it makes the saved token unreadable, so sign in again afterwards. generate with `uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `RETRO_TOKEN_FILE` | no | `.retro_refresh_token` (docker: `/data/retro_refresh_token`) | where the encrypted refresh token is saved. |
| `HOST` | no | `127.0.0.1` | address to bind |
| `PORT` | no | `8000` | port to listen on. |

## finding your retro user id

uv run python -c "from retro_sdk import Retro; print(Retro().get_user_id('<YOURRETROUSERNAME>'))"

### running locally

uv run --env-file .env retrolocation