# Personal Expense Tracker (Flask + SQLite)

## Run in VS Code terminal
Windows:
    python -m venv venv
    venv\Scripts\activate
    pip install -r requirements.txt
    python app.py
macOS/Linux:
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    python app.py

Open http://127.0.0.1:5000, register an account, and log in.
The database file (database/expenses.db) is created automatically on first run.

## Deployment (Stage 10)
1. Set environment variables: SECRET_KEY=<long random string>, PRODUCTION=1
   Generate a key with: python -c "import secrets; print(secrets.token_hex(32))"
2. Run with a production server instead of app.run:  gunicorn app:app
   (On Windows hosting, use `waitress`: pip install waitress; waitress-serve app:app)
3. Serve over HTTPS. For hosts like Render/Railway, use a persistent disk for the database folder.
