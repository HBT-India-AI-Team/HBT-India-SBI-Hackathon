"""Renders the standalone chat page served at GET /embed/{agent_id} — what a
client's site iframes in. Deliberately framework-free (inline HTML/CSS/JS, no
build step) since main.py's route has to hand back a complete page with
nothing else to assemble it, and it needs to keep working even if the React
app's own build is stale or missing. Visually matches frontend/src/index.css's
navy/gold palette by hand, since this page can't share Tailwind classes with
the React build.
"""
from __future__ import annotations

import json


def render_embed_page(agent_id: str, api_key: str, purpose: str) -> str:
    title = agent_id.replace("_", " ").title()
    # json.dumps here isn't for an HTTP body — it's escaping these three
    # server-known values for safe embedding inside the <script> block below.
    agent_id_js = json.dumps(agent_id)
    api_key_js = json.dumps(api_key)
    title_js = json.dumps(title)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    --navy-900: #122a4a; --navy-700: #1a3f70; --navy-600: #1f4f8c; --navy-100: #d9e6f4;
    --gold-500: #f0a30c; --ink: #1c2430; --paper: #f7f8fb; --line: #e2e6ee;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ height: 100%; margin: 0; }}
  body {{
    font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
    background: var(--paper); color: var(--ink);
    display: flex; flex-direction: column; height: 100vh;
  }}
  header {{
    background: var(--navy-900); color: white; padding: 14px 18px;
    display: flex; align-items: center; gap: 10px; flex-shrink: 0;
  }}
  header .mark {{
    width: 26px; height: 26px; border-radius: 7px;
    background: linear-gradient(135deg, var(--navy-600), var(--navy-700));
    display: flex; align-items: center; justify-content: center; font-size: 13px;
  }}
  header h1 {{ font-size: 14px; font-weight: 600; margin: 0; }}
  header p {{ font-size: 11px; margin: 0; color: var(--navy-100); }}
  #thread {{ flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 10px; }}
  .msg {{ max-width: 80%; padding: 9px 12px; border-radius: 12px; font-size: 13.5px; line-height: 1.45; white-space: pre-wrap; }}
  .msg.user {{ align-self: flex-end; background: var(--navy-600); color: white; border-bottom-right-radius: 3px; }}
  .msg.assistant {{ align-self: flex-start; background: white; border: 1px solid var(--line); border-bottom-left-radius: 3px; }}
  .msg.typing {{ align-self: flex-start; color: #8891a0; font-size: 12px; font-style: italic; }}
  form {{ flex-shrink: 0; display: flex; gap: 8px; padding: 12px; border-top: 1px solid var(--line); background: white; }}
  input {{
    flex: 1; border: 1px solid var(--line); border-radius: 8px; padding: 9px 12px;
    font-size: 13.5px; outline: none;
  }}
  input:focus {{ border-color: var(--navy-600); box-shadow: 0 0 0 3px rgba(31,79,140,0.12); }}
  button {{
    background: var(--navy-600); color: white; border: none; border-radius: 8px;
    padding: 0 16px; font-size: 13.5px; font-weight: 500; cursor: pointer;
  }}
  button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
</style>
</head>
<body>
<header>
  <div class="mark">✦</div>
  <div>
    <h1 id="agent-title"></h1>
    <p>Powered by the Skill-Driven Agent Runtime</p>
  </div>
</header>
<div id="thread"></div>
<form id="composer">
  <input id="input" type="text" placeholder="Type a message…" autocomplete="off" />
  <button type="submit" id="send">Send</button>
</form>

<script>
(function () {{
  var AGENT_ID = {agent_id_js};
  var API_KEY = {api_key_js};
  var TITLE = {title_js};
  document.getElementById("agent-title").textContent = TITLE;

  var STORAGE_KEY = "chat_session_" + AGENT_ID;
  var thread = document.getElementById("thread");
  var form = document.getElementById("composer");
  var input = document.getElementById("input");
  var sendBtn = document.getElementById("send");

  function addMessage(role, text) {{
    var el = document.createElement("div");
    el.className = "msg " + role;
    el.textContent = text;
    thread.appendChild(el);
    thread.scrollTop = thread.scrollHeight;
    return el;
  }}

  addMessage("assistant", "Hi! Tell me a bit about your situation and I'll help evaluate it.");

  form.addEventListener("submit", function (e) {{
    e.preventDefault();
    var message = input.value.trim();
    if (!message) return;
    input.value = "";
    sendBtn.disabled = true;
    addMessage("user", message);
    var typing = addMessage("typing", "Thinking…");

    fetch("/agents/" + AGENT_ID + "/chat", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json", "X-API-Key": API_KEY }},
      body: JSON.stringify({{ session_id: sessionStorage.getItem(STORAGE_KEY), message: message }}),
    }})
      .then(function (res) {{ return res.json(); }})
      .then(function (data) {{
        typing.remove();
        if (data.session_id) sessionStorage.setItem(STORAGE_KEY, data.session_id);
        addMessage("assistant", data.reply || "Sorry, something went wrong.");
      }})
      .catch(function () {{
        typing.remove();
        addMessage("assistant", "Sorry, I couldn't reach the server. Please try again.");
      }})
      .finally(function () {{ sendBtn.disabled = false; input.focus(); }});
  }});
}})();
</script>
</body>
</html>
"""
