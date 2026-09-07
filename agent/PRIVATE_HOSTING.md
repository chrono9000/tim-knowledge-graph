# Private graph access design

## Decision

The private master graph and its viewer stay local by default. Run `python -m agent view-private`; it binds only to loopback (`127.0.0.1`), does not copy the graph into a web directory, makes no external requests, disables browser caching, and uses a strict route allowlist.

GitHub Pages remains an optional sanitized public view. Repository visibility is not treated as an access control for Pages; [GitHub explicitly warns that Pages sites are public even when their repository is private](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site).

## Options

| Option | Best for | Security boundary | Trade-off |
| --- | --- | --- | --- |
| Local-only viewer (recommended first) | Tim on one computer | Operating-system login plus loopback-only server | Simplest and least exposed; not remotely shared |
| [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve) (recommended for named users) | Tim and a small authorized group | Tailnet identity, device enrollment, HTTPS, and access rules | Each user/device needs tailnet access; Tim's host must be online |
| [Cloudflare Tunnel + Access](https://developers.cloudflare.com/cloudflare-one/setup/secure-private-apps/) | Browser access without a VPN client | Cloudflare Access identity policy in front of a tunnel | More moving parts, requires a domain/account and careful policy testing |
| Self-hosted VPN/reverse proxy | Teams with existing infrastructure | Operator-managed VPN, TLS, identity and firewall | Highest maintenance burden |

Do not use Tailscale Funnel, a public object store, a public static host, or GitHub Pages for private data. External hosting is not configured by this repository. Tim must approve the mode and authorize each user before sharing.

## Approval control

`stage-chatgpt` refuses to persist proposals until `approve-access` creates a local ignored approval record. Validation and dry-run remain read-only. Changing the approved design is an explicit local action; it does not configure Tailscale or Cloudflare automatically.
