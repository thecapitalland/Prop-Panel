# Security Policy

Prop-Panel processes trading-account data locally and contains MQL5 code capable of live order management. Treat configuration and runtime files as sensitive.

## Do not commit

- Broker or prop-account credentials
- Real account numbers or personally identifying account metadata
- Telegram/API tokens
- Generated bridge JSON containing live account details
- Local `History/meta.json` files
- Private Strategy Tester reports containing sensitive account information

Runtime and cache paths are excluded through `.gitignore`; review changes before every public push.

## Reporting a vulnerability

Do not publish credentials, account identifiers, or exploitable trading behavior in a public issue. Contact the repository owner through GitHub with a minimal disclosure and request a private channel for sensitive details.

## Scope note

The dashboard is designed for localhost use. Exposing it to another host or network without adding an explicit authentication and transport-security layer is outside the supported security model.