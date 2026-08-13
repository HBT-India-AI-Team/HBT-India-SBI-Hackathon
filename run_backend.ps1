# Output is teed to uvicorn_out.log as well as the console, and appended
# rather than truncated, because uvicorn's access lines are the only record of
# what a client actually called. /admin/api-surface reads this file to show
# which paths are being hit and which of them we do not serve — with the log
# going only to a console that gets closed, a client on the wrong URL stays
# invisible to everyone.
python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8080 *>&1 |
    Tee-Object -FilePath uvicorn_out.log -Append
