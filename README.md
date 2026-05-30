# shorturl

URL shortener for `shorturl.janapriyahomes.com`. FastAPI + Postgres + Jinja admin.

## Layout
- `app/main.py` — all routes (public redirect, admin auth, link CRUD, QR)
- `app/{config,db,auth,slug}.py` — settings, PG pool, session cookies, slug helpers
- `app/templates/`, `app/static/` — admin UI
- `scripts/init_db.sql` — schema (one table: `links`)
- `scripts/hash_password.py` — generate bcrypt hash for `ADMIN_PASSWORD_HASH`
- `deploy/shorturl-api.service` — systemd unit (uvicorn on 127.0.0.1:8401)
- `deploy/shorturl.nginx.conf` — nginx vhost

## Dev
```
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python scripts/hash_password.py     # generate hash
cp .env.example .env && chmod 600 .env        # fill DATABASE_URL, hash, secret
psql "$DATABASE_URL" -f scripts/init_db.sql
.venv/bin/uvicorn app.main:app --reload --port 8401
```

## Deploy
```
sudo cp deploy/shorturl-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now shorturl-api
sudo cp deploy/shorturl.nginx.conf /etc/nginx/conf.d/shorturl.conf
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d shorturl.janapriyahomes.com    # after DNS resolves
```
