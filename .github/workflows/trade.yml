name: Trading bot (simulation)

on:
  schedule:
    - cron: "0 * * * *"   # toutes les heures
  workflow_dispatch:       # lancement manuel possible

permissions:
  contents: write

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install yfinance
      - run: python trading_bot.py
      - name: Sauvegarder le portefeuille
        run: |
          git config user.name "trading-bot"
          git config user.email "bot@users.noreply.github.com"
          git add portfolio.json
          git commit -m "Mise à jour du portefeuille" || echo "Rien à commiter"
          git push
