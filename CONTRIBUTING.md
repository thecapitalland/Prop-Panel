# Contributing

Contributions are welcome when they preserve the project's local-first and safety-oriented design.

## Before changing behavior

- Keep the dashboard bound to localhost unless a change explicitly introduces and documents an authentication/security model.
- Do not make the dashboard start, stop, or kill a live MT5 terminal implicitly.
- Keep fast Python simulation clearly separated from real EA/Strategy Tester behavior.
- Never commit credentials, account exports, tokens, generated market-data caches, or personal account metadata.
- Treat changes to live execution or drawdown guards as high-risk behavior changes.

## Verification

```powershell
python -m pip install -r requirements.txt
Copy-Item History\meta.example.json History\meta.json
cd dashboard
python -m unittest test_dashboard.py test_tester_report.py -v
```

Keep commits focused and document any behavior difference between the Python simulator and the MQL5 EA.