# Crime Intelligence & Predictive Policing System

Official-style Flask dashboard for:

- FIR visualization on maps
- hotspot detection
- patrol route planning
- crime analytics
- women safety analysis
- emergency alert dispatch guidance

## Run Locally

Use your installed Python:

```powershell
& "C:\Users\Sri Chandhan\AppData\Local\Programs\Python\Python313\python.exe" app.py
```

Open:

```text
http://127.0.0.1:5000
```

Default demo login:

- Username: `admin`
- Password: `police123`

## Files Added For Hosting

- `requirements.txt`
- `render.yaml`
- `.gitignore`

These are already configured for Render deployment.

## Option 1: Upload To GitHub Using The Website

If Git is not installed on your computer, use this method.

1. Go to [https://github.com](https://github.com)
2. Create a new repository
3. Name it something like `crime-intelligence-dashboard`
4. Choose `Public` or `Private`
5. Click `Create repository`
6. Click `uploading an existing file`
7. Drag the whole project files into GitHub, except:
   - `.venv`
   - `__pycache__`
8. Commit the upload

Important:

- Keep `templates/` and `static/`
- Keep `render.yaml`
- Keep `requirements.txt`

## Option 2: Push To GitHub Using Git Commands

If Git is installed on your system, open a terminal in this folder and run:

```powershell
git init
git add .
git commit -m "Initial deployment-ready crime intelligence dashboard"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
git push -u origin main
```

Replace:

- `YOUR_USERNAME`
- `YOUR_REPOSITORY`

Example:

```powershell
git remote add origin https://github.com/srichandhan/crime-intelligence-dashboard.git
```

## Deploy On Render

1. Open [https://render.com](https://render.com)
2. Sign in with GitHub
3. Click `New +`
4. Click `Blueprint`
5. Select your GitHub repository
6. Render will detect `render.yaml`
7. Click `Apply`

After deploy:

1. Open your Render service
2. Go to `Environment`
3. Change:
   - `APP_USERNAME`
   - `APP_PASSWORD`
4. Save and redeploy if needed

## Important Hosting Note

This project currently stores FIR updates in CSV files.

On many online hosts:

- CSV changes may not be permanent
- new FIR entries may disappear after restart or redeploy

For full production hosting, the next upgrade should be:

- SQLite, PostgreSQL, or MySQL database storage

## Recommended Next Upgrade

To make this truly online-ready, add:

1. database-backed FIR storage
2. hashed user passwords
3. admin and officer roles
4. persistent alert history
