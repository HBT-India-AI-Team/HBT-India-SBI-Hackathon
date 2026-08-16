# This machine's user-wide OLLAMA_HOST (10.6.16.111, no scheme) points at a
# LAN box that isn't always reachable and pre-empts backend/.env (dotenv
# does not override an already-set env var). Force it here so this project
# always talks to the ngrok tunnel in backend/.env regardless of what's set
# system-wide -- update backend/.env's OLLAMA_HOST when that tunnel rotates.
if (Test-Path "$PSScriptRoot\backend\.env") {
    Get-Content "$PSScriptRoot\backend\.env" | ForEach-Object {
        if ($_ -match '^\s*OLLAMA_HOST\s*=\s*(.+?)\s*$') {
            $env:OLLAMA_HOST = $Matches[1]
        }
    }
}

# Output is teed to uvicorn_out.log as well as the console, and appended
# rather than truncated, because uvicorn's access lines are the only record of
# what a client actually called. /admin/api-surface reads this file to show
# which paths are being hit and which of them we do not serve — with the log
# going only to a console that gets closed, a client on the wrong URL stays
# invisible to everyone.
python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8080 *>&1 |
    Tee-Object -FilePath uvicorn_out.log -Append
