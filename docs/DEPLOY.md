# Deploying the demo page

The page in `web/` is static (HTML + `data.json` + images). Live at https://jejakasap.mshadianto.id,
served by Caddy on the VPS that hosts the other mshadianto.id subdomains.

```
jejakasap.mshadianto.id {
	root * /var/www/jejakasap
	encode gzip
	file_server
	header X-Content-Type-Options nosniff
}
```

DNS: Cloudflare A record `jejakasap` -> the VPS, DNS only (Caddy gets the TLS certificate itself).

To update: wrap `web/index.html` in a full HTML document (doctype, charset, viewport), copy it with
`data.json`, `img/` and `og.jpg` to `/var/www/jejakasap/`. No reload needed for content changes.
