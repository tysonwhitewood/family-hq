# Two-Way Mattermost — Implementation Plan (Step 2 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Family HQ bot reads the Family Finance channel every minute, acts on short commands, reads bank-app screenshots and bill photos, answers free-form money questions, and confirms everything back in the channel.

**Architecture:** `mattermost.py` gains read/download/reaction calls. New `conversation.py` holds the command parser, the image reader prompt/parse, the free-form answer builder and `handle_post`, all pure or driven through injected callables. `reminders.py` gains `poll_once` (watermark, allowed users, dedupe, delivery) and runs it from `scheduler_tick`; it also gains the Monday PropVesting check and one-a-day set-up questions. `app.py` extends `llm_chat` with image input, adds `auto_pay` to obligations, and exposes poll/simulate routes. `dashboard.html` gets the direct-debit checkbox and shows replies in the Conversation card.

**Tech Stack:** Python 3.12, Flask, sqlite3, `requests`, Anthropic SDK (images) with OpenRouter fallback, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-09-obligations-and-mattermost-design.md` (sections "Mattermost conversation", "Screenshot reading", "Polling", and "Step 2 amendments").

## Global Constraints

- Run tests with `.venv/bin/python -m unittest discover -s tests -q`. Baseline: 204 pass. Never leave the suite red at a commit.
- Work on branch `feature/two-way-mattermost`; `main` auto-deploys.
- No secrets in the repo. Bot token and channel id are env vars already set in Coolify.
- Australian English in every user-facing string.
- The bot never acts on posts from users outside `mattermost.allowed_users` and never on its own posts.
- Arithmetic stays in `obligations.py`. The LLM only reads images and free text and writes prose answers.
- Every reply the bot sends is logged in `reminder_log` with `kind='reply'` and `dedupe_key='reply:<post_id>'`; a post is never handled twice.
- Commit after each task with the attribution block:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01AjD4c8Kp1UGQPXfTM8jC9X
  ```

## File Structure

| File | Responsibility |
|---|---|
| `mattermost.py` (modify) | `me()`, `posts_since()`, `download_file()`, `add_reaction()`, `users_by_ids()`. |
| `conversation.py` (new) | `account_aliases`, `parse_money`, `parse_command`, `compose_help`, `IMAGE_PROMPT`, `parse_image_result`, `money_context`, `answer_question`, `handle_post`. |
| `reminders.py` (modify) | `poll_once`, allowed-user resolution and cache, watermark, backoff; Monday PropVesting check; set-up questions; overdue in `position`; auto-pay occurrences marked paid. |
| `obligations.py` (modify) | `compose_lead_warning` auto-pay wording; `compose_setup_question`, `compose_propvesting_check`. |
| `app.py` (modify) | `llm_chat(images=...)`, `llm_vision_available()`, `auto_pay` column migration + API/validation, `/api/mattermost/poll`, `/api/mattermost/simulate`, status shows last poll. |
| `dashboard.html` (modify) | Direct-debit checkbox in the obligation modal; Conversation card shows replies; Settings shows last poll. |
| `data/config.json` (modify) | `allowed_users: ["tawhai", "mum"]`, `aliases` on accounts. |
| `README.md`, `docs/cash-flow-operations.md` (modify) | Reply vocabulary, aliases, direct debits, vision fallback. |
| `tests/test_mattermost.py`, `tests/test_conversation.py` (new), `tests/test_reminders.py`, `tests/test_obligations_api.py`, `tests/test_obligations.py` | Coverage per task. |

---

### Task 1: Mattermost client can read, download and react

**Files:** `mattermost.py`, `tests/test_mattermost.py`

**Interfaces (Produces):**
- `MattermostClient.me() -> dict` (cached after first call)
- `MattermostClient.posts_since(since_ms: int) -> list[dict]` — posts sorted by `create_at` ascending; each has `id, user_id, message, create_at, file_ids, root_id, type`.
- `MattermostClient.download_file(file_id: str) -> tuple[bytes, str, str]` — `(content, mime_type, name)`; raises `MattermostError` over 10 MB or for non-image mime types.
- `MattermostClient.add_reaction(post_id: str, emoji_name: str = 'white_check_mark') -> None`
- `MattermostClient.users_by_ids(ids: list[str]) -> dict[str, str]` — id → username.

- [ ] **Step 1: Tests.** Extend `FakeMattermost` in `tests/test_mattermost.py`:

```python
    def do_GET(self):
        FakeMattermost.requests.append(("GET", self.path, dict(self.headers), None))
        if self.path == "/api/v4/system/ping":
            return self._reply(200, {"status": "OK"})
        if self.path == "/api/v4/users/me":
            return self._reply(200, {"id": "botid", "username": "familyhq"})
        if self.path.startswith("/api/v4/channels/chan/posts"):
            return self._reply(200, {"order": ["p2", "p1"], "posts": {
                "p1": {"id": "p1", "user_id": "u1", "message": "gst 9262", "create_at": 100, "file_ids": [], "root_id": "", "type": ""},
                "p2": {"id": "p2", "user_id": "u2", "message": "", "create_at": 200, "file_ids": ["f1"], "root_id": "", "type": ""},
            }})
        if self.path == "/api/v4/files/f1/info":
            return self._reply(200, {"id": "f1", "name": "shot.png", "mime_type": "image/png", "size": 4})
        if self.path == "/api/v4/files/f1":
            self.send_response(200); self.send_header("Content-Type", "image/png"); self.send_header("Content-Length", "4"); self.end_headers(); self.wfile.write(b"PNG!"); return
        if self.path == "/api/v4/files/big/info":
            return self._reply(200, {"id": "big", "name": "x.png", "mime_type": "image/png", "size": 20 * 1024 * 1024})
        if self.path == "/api/v4/files/doc/info":
            return self._reply(200, {"id": "doc", "name": "x.pdf", "mime_type": "application/pdf", "size": 4})
        return self._reply(404, {"message": "not found"})
```
and in `do_POST` before the 404: `if self.path == "/api/v4/reactions": return self._reply(200, body)` and `if self.path == "/api/v4/users/ids": return self._reply(200, [{"id": i, "username": {"u1": "tawhai", "u2": "mum"}.get(i, "x")} for i in body])`.

Add tests:

```python
    def test_posts_since_returns_ascending_and_flattened(self):
        client = mattermost.MattermostClient(self.base, token="tok", channel_id="chan")
        posts = client.posts_since(50)
        self.assertEqual([p["id"] for p in posts], ["p1", "p2"])
        self.assertIn("since=50", FakeMattermost.requests[0][1])
        self.assertEqual(posts[1]["file_ids"], ["f1"])

    def test_download_file_returns_bytes_and_mime(self):
        client = mattermost.MattermostClient(self.base, token="tok", channel_id="chan")
        content, mime, name = client.download_file("f1")
        self.assertEqual((content, mime, name), (b"PNG!", "image/png", "shot.png"))

    def test_download_rejects_large_and_non_image_files(self):
        client = mattermost.MattermostClient(self.base, token="tok", channel_id="chan")
        with self.assertRaises(mattermost.MattermostError):
            client.download_file("big")
        with self.assertRaises(mattermost.MattermostError):
            client.download_file("doc")

    def test_me_is_cached_and_reaction_and_users_work(self):
        client = mattermost.MattermostClient(self.base, token="tok", channel_id="chan")
        self.assertEqual(client.me()["id"], "botid")
        client.me()
        self.assertEqual(sum(1 for r in FakeMattermost.requests if r[1] == "/api/v4/users/me"), 1)
        client.add_reaction("p1")
        method, path, _, body = FakeMattermost.requests[-1]
        self.assertEqual((method, path, body["emoji_name"], body["user_id"]), ("POST", "/api/v4/reactions", "white_check_mark", "botid"))
        self.assertEqual(client.users_by_ids(["u1", "u2"]), {"u1": "tawhai", "u2": "mum"})

    def test_read_calls_need_a_bot_token(self):
        client = mattermost.MattermostClient(self.base, webhook_url=f"{self.base}/hooks/abc")
        with self.assertRaises(mattermost.MattermostError):
            client.posts_since(0)
```

- [ ] **Step 2: Implement** in `mattermost.py`:

```python
MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_MIME_TYPES = {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}

    def _require_bot(self):
        if not self.can_read:
            raise MattermostError('This call needs a bot token and channel id')

    def me(self) -> dict:
        if getattr(self, '_me', None) is None:
            self._require_bot()
            self._me = self._request('GET', f'{self.base_url}/api/v4/users/me', headers=self._headers()).json()
        return self._me

    def posts_since(self, since_ms: int) -> list[dict]:
        self._require_bot()
        data = self._request('GET', f'{self.base_url}/api/v4/channels/{self.channel_id}/posts',
                             headers=self._headers(), params={'since': int(since_ms)}).json()
        posts = [data['posts'][pid] for pid in data.get('order', []) if pid in data.get('posts', {})]
        keep = ('id', 'user_id', 'message', 'create_at', 'file_ids', 'root_id', 'type')
        return sorted(({k: p.get(k) for k in keep} for p in posts), key=lambda p: p['create_at'])

    def download_file(self, file_id: str) -> tuple[bytes, str, str]:
        self._require_bot()
        info = self._request('GET', f'{self.base_url}/api/v4/files/{file_id}/info', headers=self._headers()).json()
        mime = (info.get('mime_type') or '').lower()
        if mime not in IMAGE_MIME_TYPES:
            raise MattermostError(f'Not an image: {mime or "unknown type"}')
        if int(info.get('size') or 0) > MAX_IMAGE_BYTES:
            raise MattermostError('Image larger than 10 MB')
        content = self._request('GET', f'{self.base_url}/api/v4/files/{file_id}', headers=self._headers()).content
        return content, mime, info.get('name') or file_id

    def add_reaction(self, post_id: str, emoji_name: str = 'white_check_mark') -> None:
        self._require_bot()
        self._request('POST', f'{self.base_url}/api/v4/reactions', headers=self._headers(),
                      json={'user_id': self.me()['id'], 'post_id': post_id, 'emoji_name': emoji_name})

    def users_by_ids(self, ids: list[str]) -> dict[str, str]:
        self._require_bot()
        if not ids:
            return {}
        rows = self._request('POST', f'{self.base_url}/api/v4/users/ids', headers=self._headers(), json=list(ids)).json()
        return {u['id']: u['username'] for u in rows}
```

Set `self._me = None` in `__init__`. Note `posts?since=` also returns edited posts; callers dedupe by id.

- [ ] **Step 3:** Run `.venv/bin/python -m unittest tests.test_mattermost -q` → 11 pass. Commit `feat: Mattermost client reads posts, downloads images, reacts`.

---

### Task 2: `llm_chat` accepts images; vision availability

**Files:** `app.py`, `tests/test_obligations_api.py`

**Interfaces (Produces):** `llm_chat(messages, system='', max_tokens=1024, images=None)` where `images` is a list of `{'media_type': 'image/png', 'data': '<base64>'}` attached to the last user message; `llm_vision_available() -> bool`; `OPENROUTER_VISION_MODELS`.

- [ ] **Step 1: Tests** (append to `tests/test_obligations_api.py`):

```python
class LlmImageTests(unittest.TestCase):
    def test_anthropic_path_sends_image_blocks(self):
        captured = {}
        class FakeMessages:
            def create(self, **kwargs):
                captured.update(kwargs)
                class R: content = [type("T", (), {"text": "ok"})()]
                return R()
        class FakeClient:
            def __init__(self, api_key): self.messages = FakeMessages()
        fake_module = type("M", (), {"Anthropic": FakeClient})
        with patch.dict("sys.modules", {"anthropic": fake_module}), \
             patch.object(family_app, "_anthropic_key", return_value="k"):
            out = family_app.llm_chat([{"role": "user", "content": "read this"}], system="s",
                                      images=[{"media_type": "image/png", "data": "QUJD"}])
        self.assertEqual(out, "ok")
        blocks = captured["messages"][-1]["content"]
        self.assertEqual(blocks[0]["type"], "image")
        self.assertEqual(blocks[0]["source"], {"type": "base64", "media_type": "image/png", "data": "QUJD"})
        self.assertEqual(blocks[-1], {"type": "text", "text": "read this"})

    def test_openrouter_path_uses_vision_models_for_images(self):
        seen = []
        class FakeResp:
            def __init__(self, payload): self._p = payload
            def read(self): return json.dumps({"choices": [{"message": {"content": "seen"}}]}).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False
        def fake_urlopen(req, timeout=30):
            seen.append(json.loads(req.data))
            return FakeResp(None)
        with patch.object(family_app, "_anthropic_key", return_value=""), \
             patch.object(family_app, "_openrouter_key", return_value="or"), \
             patch.object(family_app.urllib.request, "urlopen", fake_urlopen):
            out = family_app.llm_chat([{"role": "user", "content": "read"}], images=[{"media_type": "image/jpeg", "data": "QUJD"}])
        self.assertEqual(out, "seen")
        self.assertEqual(seen[0]["model"], family_app.OPENROUTER_VISION_MODELS[0])
        parts = seen[0]["messages"][-1]["content"]
        self.assertEqual(parts[0]["type"], "image_url")
        self.assertTrue(parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_vision_available_tracks_keys(self):
        with patch.object(family_app, "_anthropic_key", return_value=""), patch.object(family_app, "_openrouter_key", return_value=""):
            self.assertFalse(family_app.llm_vision_available())
        with patch.object(family_app, "_anthropic_key", return_value=""), patch.object(family_app, "_openrouter_key", return_value="x"):
            self.assertTrue(family_app.llm_vision_available())
```

- [ ] **Step 2: Implement.** Replace `llm_chat` in `app.py`:

```python
OPENROUTER_TEXT_MODELS = [
    'meta-llama/llama-3.3-70b-instruct:free',
    'google/gemma-3-27b-it:free',
    'mistralai/mistral-7b-instruct:free',
]
OPENROUTER_VISION_MODELS = [
    'google/gemma-3-27b-it:free',
    'meta-llama/llama-3.2-11b-vision-instruct:free',
]


def llm_vision_available():
    return bool(_anthropic_key() or _openrouter_key())


def _with_images(messages: list, images: list | None, style: str) -> list:
    """Attach images to the last user message in Anthropic or OpenAI block style."""
    if not images:
        return messages
    messages = [dict(m) for m in messages]
    last = messages[-1]
    text = last['content'] if isinstance(last['content'], str) else ''
    if style == 'anthropic':
        blocks = [{'type': 'image', 'source': {'type': 'base64', 'media_type': i['media_type'], 'data': i['data']}} for i in images]
        blocks.append({'type': 'text', 'text': text})
    else:
        blocks = [{'type': 'image_url', 'image_url': {'url': f"data:{i['media_type']};base64,{i['data']}"}} for i in images]
        blocks.append({'type': 'text', 'text': text})
    last['content'] = blocks
    return messages


def llm_chat(messages: list, system: str = '', max_tokens: int = 1024, images: list | None = None) -> str:
    """Call Claude via Anthropic SDK, or fall back to OpenRouter free models (vision-capable ones when images are given)."""
    anthropic_key = _anthropic_key()
    openrouter_key = _openrouter_key()

    if anthropic_key:
        import anthropic
        client = anthropic.Anthropic(api_key=anthropic_key)
        kwargs = dict(model='claude-sonnet-4-6', max_tokens=max_tokens, messages=_with_images(messages, images, 'anthropic'))
        if system:
            kwargs['system'] = system
        response = client.messages.create(**kwargs)
        return response.content[0].text

    if openrouter_key:
        _models = OPENROUTER_VISION_MODELS if images else OPENROUTER_TEXT_MODELS
        last_err = None
        for model in _models:
            payload = json.dumps({
                'model': model,
                'messages': ([{'role': 'system', 'content': system}] if system else []) + _with_images(messages, images, 'openai'),
                'max_tokens': max_tokens,
            }).encode()
            req = urllib.request.Request(
                'https://openrouter.ai/api/v1/chat/completions', data=payload,
                headers={'Authorization': f'Bearer {openrouter_key}', 'Content-Type': 'application/json',
                         'HTTP-Referer': 'https://family.edencommercial.au'},
                method='POST',
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = json.loads(resp.read())
                    return data['choices'][0]['message']['content']
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code not in (429, 404, 400):
                    raise
        raise last_err

    raise ValueError('No LLM configured — set ANTHROPIC_API_KEY or OPENROUTER_API_KEY')
```

Keep the existing `llm_available()`.

- [ ] **Step 3:** Run the suite → 207 pass. Commit `feat: llm_chat accepts images with a vision fallback on OpenRouter`.

---

### Task 3: Conversation module — commands, image reading, free-form answers

**Files:** `conversation.py` (new), `data/config.json` (aliases, allowed users), `tests/test_conversation.py` (new)

**Interfaces (Produces):**
- `account_aliases(settings) -> dict[str, str]` alias → account key (lower-case; from `aliases`, the key, and words of `display`).
- `parse_money(text) -> float | None` handles `$9,262.50`, `9262`, `5k`.
- `parse_command(text, settings) -> dict | None` with `kind` in `done, paid, skip, yes, status, help, balance, receipt, receipt_total, bill` and fields: `balance: account_key, amount`; `receipt: amount`; `receipt_total: amount`; `bill: name, due_date (ISO or None), amount (or None)`. Returns `None` for free text.
- `compose_help() -> str`
- `IMAGE_PROMPT: str`, `parse_image_result(text) -> dict` returning `{'kind': 'balances'|'bill'|'receipts'|'other', 'balances': [...], 'bill': {...}|None, 'receipts': [...], 'note': str}`; tolerant of code fences and junk.
- `money_context(service, today) -> str` — plain-text context for the LLM.
- `answer_question(llm, question, context) -> str`.
- `handle_post(service, post, settings, llm=None, images=None, today=None) -> dict(reply: str|None, acted: bool)` — `images` is a list of `(bytes, mime, name)` already downloaded by the caller; `llm` is a callable `(messages, system, images=None) -> str`.

Handling rules (each returns a reply string):
- `done` → the latest `monthly_setaside` in `reminder_log` gets a `reminder_state['setaside_done:<ym>']='post_id'`; reply "Marked September set-aside as done."
- `paid` → nearest open occurrence (any remindable obligation, due within ±30 days) → `paid`; reply names it. `skip` → `skipped`.
- `yes` → if a PropVesting-style once obligation (name contains 'PropVesting') has no anchor: set `anchor_date=today`, regenerate; reply "PropVesting is registered: pay $7,756 now. I'll remind you tomorrow if it is still open." Otherwise "Yes to what? …".
- `status` → `compose_weekly_position(position targets, upcoming ≤30 days, today, birthdays)`.
- `help` → `compose_help()`.
- `balance` → insert `account_balances` (source `typed`, post id); reply "Got it: EComm GST $9,262 as at today."
- `receipt` → add to `receipts_log[current month]` detail list and total (create if absent, starting from 0, *not* the assumed retainer); regenerate; reply with month total and new set-aside figure via `monthly_setaside`.
- `receipt_total` → replace the month total.
- `bill` → find obligation whose name contains all words of `name` (case-insensitive) else create `pending_confirmation` once obligation; set amount/anchor as given; regenerate; reply.
- images → for each image call `llm` with `IMAGE_PROMPT` (+ account list) and parse. `balances` → insert rows for matched accounts (`account_key` non-null and in configured keys), reply "Got it: …" listing them and any unmatched names; `bill` → same as bill command with `pending_confirmation`; `receipts` → add each to current month; `other` → "I couldn't find balances or a bill in that image."
- free text → `answer_question(llm, text, money_context(...))`; acted=False (no ✅).
- LLM missing (`llm is None`) for image/free text → reply explaining typed replies still work.

- [ ] **Step 1: Tests.** Create `tests/test_conversation.py` using the same temp-DB pattern as `tests/test_reminders.py` (import app under `patch("threading.Thread.start")`, write config with accounts incl. `aliases`), plus:

```python
class ParseTests(unittest.TestCase):
    SETTINGS = {"accounts": [
        {"key": "ecomm_gst", "display": "EComm GST", "aliases": ["gst", "ecomm"]},
        {"key": "ing_home", "display": "ING Home", "aliases": ["home"]},
        {"key": "eden_operating", "display": "Eden Commercial", "aliases": ["eden ops"]},
    ]}
    def test_money(self):
        self.assertEqual(conversation.parse_money("$9,262.50"), 9262.5)
        self.assertEqual(conversation.parse_money("5k"), 5000.0)
        self.assertIsNone(conversation.parse_money("soon"))
    def test_keywords(self):
        for word, kind in [("done", "done"), ("Paid ", "paid"), ("skip", "skip"), ("yes", "yes"), ("status", "status"), ("help", "help"), ("?", "help")]:
            self.assertEqual(conversation.parse_command(word, self.SETTINGS)["kind"], kind, word)
    def test_balances_and_receipts(self):
        self.assertEqual(conversation.parse_command("gst 9262", self.SETTINGS), {"kind": "balance", "account_key": "ecomm_gst", "amount": 9262.0})
        self.assertEqual(conversation.parse_command("ING Home $2,142.41", self.SETTINGS)["account_key"], "ing_home")
        self.assertEqual(conversation.parse_command("eden 5280", self.SETTINGS), {"kind": "receipt", "amount": 5280.0})
        self.assertEqual(conversation.parse_command("eden total 24,500", self.SETTINGS), {"kind": "receipt_total", "amount": 24500.0})
        self.assertEqual(conversation.parse_command("eden ops 6783", self.SETTINGS)["account_key"], "eden_operating")
    def test_bill_phrases(self):
        cmd = conversation.parse_command("rates due 27 Feb 2027 1614", self.SETTINGS)
        self.assertEqual((cmd["kind"], cmd["name"], cmd["due_date"], cmd["amount"]), ("bill", "rates", "2027-02-27", 1614.0))
        cmd = conversation.parse_command("rego 965", self.SETTINGS)
        self.assertEqual((cmd["kind"], cmd["name"], cmd["due_date"], cmd["amount"]), ("bill", "rego", None, 965.0))
    def test_free_text_is_none(self):
        self.assertIsNone(conversation.parse_command("can we afford the driveway this month?", self.SETTINGS))
    def test_parse_image_result_tolerates_fences(self):
        out = conversation.parse_image_result('```json\n{"kind":"balances","balances":[{"account_key":"ecomm_gst","name_seen":"EComm GST","balance":9048.03,"available":92.03,"as_of":null}]}\n```')
        self.assertEqual(out["kind"], "balances"); self.assertEqual(out["balances"][0]["balance"], 9048.03)
        self.assertEqual(conversation.parse_image_result("nonsense")["kind"], "other")
```
and a `HandlePostTests(ReminderCase)`-style class covering: balance stores a row with `mattermost_post_id`; `paid` marks the nearest occurrence; `receipt` creates the month total and the reply quotes the set-aside; image balances stored via a fake `llm` returning JSON; free text goes to the fake `llm` with context containing "EComm GST"; `llm=None` with an image yields the explanatory reply; `yes` with no PropVesting anchor sets today.

- [ ] **Step 2: Implement `conversation.py`** (full code in the task's commit; key pieces):

```python
KEYWORDS = {'done': 'done', 'paid': 'paid', 'skip': 'skip', 'yes': 'yes', 'status': 'status',
            'position': 'status', 'help': 'help', '?': 'help'}
MONEY_RE = re.compile(r'\$?\s*(\d[\d,]*(?:\.\d{1,2})?)\s*(k)?', re.I)
DATE_RE = re.compile(r'(\d{4}-\d{2}-\d{2})|(\d{1,2})[ /-]([A-Za-z]{3,9}|\d{1,2})(?:[ /-](\d{2,4}))?')
IMAGE_PROMPT = """You are reading a photo or screenshot for a family finance app. Reply with JSON only, no prose.
Schema: {"kind": "balances"|"bill"|"receipts"|"other",
 "balances": [{"account_key": string|null, "name_seen": string, "balance": number, "available": number|null, "as_of": "YYYY-MM-DD"|null}],
 "bill": {"payee": string, "amount": number|null, "due_date": "YYYY-MM-DD"|null, "description": string}|null,
 "receipts": [{"date": "YYYY-MM-DD"|null, "amount": number, "description": string}],
 "note": string}
Known accounts (match by name or the digits shown; use the key, else null): {accounts}
Rules: balances are the "Current"/"Balance" column, available is the "Available" column when shown; negative for money owed; dates as ISO; today is {today}."""
```
`account_aliases` builds `{alias: key}` from `aliases`, the key, `key.replace('_',' ')`, and the full lower-cased `display`. `parse_command` order: keyword → `eden total <amt>` → `eden <amt>` → `<alias> <amt>` (longest alias first) → `<name> due <date> [<amt>]` / `<name> <amt>` when the first word is not an alias and the text has ≤ 4 words → `None`. Australian day-first dates; a year-less date rolls to the next occurrence.

`money_context` returns lines: today, each target (`display: should hold X, holds Y (age), shortfall`), next 30 days (`date name amount [overdue]`), receipts (`month: amount (reported/assumed)`), monthly budget totals from `budget_targets` by type, and the rates in settings. `answer_question` system prompt: "You are Family HQ's money assistant for Tyson and Robyn. Australian English. Answer in under 120 words using only the figures in the context; if the context lacks what is needed, say what to post (a screenshot or a figure). Never invent numbers."

- [ ] **Step 3: config.** In `data/config.json` set `"allowed_users": ["tawhai", "mum"]` and add `aliases` to accounts: eden_operating `["eden ops", "eden commercial", "eden operating"]`, ecomm_gst `["gst", "ecomm", "ecomm gst", "tax"]`, cba_utilities `["utilities", "vehicles"]`, ing_everyday `["everyday", "orange", "ing everyday"]`, ing_emergency `["emergency", "food buffer"]`, ing_home `["home", "ing home"]`, ing_savings `["savings", "ing savings"]`, gsb_everyday `["gsb", "gsb everyday", "mortgage account"]`, gsb_mortgage `["mortgage balance", "loan"]`.

- [ ] **Step 4:** Run suite; commit `feat: conversation parser, image reading and free-form answers`.

---

### Task 4: Polling, PropVesting check, set-up questions, overdue, auto-pay

**Files:** `reminders.py`, `obligations.py`, `app.py` (migration + validation), `tests/test_reminders.py`, `tests/test_obligations.py`, `tests/test_obligations_api.py`

**Interfaces (Produces):**
- `ReminderService.__init__(..., llm=None, images_fn=None)`; `poll_once(today=None) -> dict(processed, replied, skipped, reason)`; `allowed_user_ids() -> set[str]` (cached per service); state keys `mm_last_post_create_at`, `mm_poll_failures`, `mm_last_poll_at`.
- `scheduler_tick` runs `poll_once` every tick when `client.can_read`, with backoff: after 3 consecutive failures only every 5th tick.
- `pending_daily_messages` adds `propvesting_check:<date>` on Mondays while a PropVesting obligation has no anchor, and `setup_question:<obligation_id>` for the first `pending_confirmation` obligation without one sent (one per day, only in the first 14 days after seed or after creation).
- `position()['upcoming']` includes open occurrences due in the last 30 days with `days_out < 0` and `overdue: True`.
- `regenerate_occurrences` marks occurrences of `auto_pay=1` obligations as `paid` (`state_changed_by='auto_pay'`) once `due_date < today`.
- `obligations.compose_lead_warning` auto-pay wording; `compose_propvesting_check()`, `compose_setup_question(obligation) -> str`.
- `app.init_db` adds column `auto_pay INTEGER NOT NULL DEFAULT 0` if missing (PRAGMA check); `_validate_obligation` accepts `auto_pay`; list/save/dashboard carry it.

- [ ] **Step 1: Tests** (abridged; write each fully):
  - `test_poll_sets_watermark_to_now_on_first_run_and_replays_nothing`
  - `test_poll_handles_allowed_user_commands_and_logs_reply` (FakeClient with `posts_since` returning a `gst 9262` post from `u1`, `users_by_ids` → `tawhai`; assert reply posted, reaction added, `reminder_log` has `reply:p1`, balance row stored, watermark advanced)
  - `test_poll_ignores_bot_and_unknown_users`
  - `test_poll_never_handles_the_same_post_twice`
  - `test_poll_downloads_images_and_passes_them_to_the_handler`
  - `test_scheduler_tick_polls_when_bot_can_read_and_backs_off_after_failures`
  - `test_monday_propvesting_check_until_anchor_set`
  - `test_one_setup_question_per_day_for_pending_items`
  - `test_overdue_open_occurrence_appears_with_negative_days`
  - `test_auto_pay_occurrence_marked_paid_after_due` and `test_auto_pay_warning_wording` (obligations test: text contains "direct debit" and not "Reply *paid*").
  - API: `test_auto_pay_column_exists_after_migration_of_old_schema` (create a DB with the old table, run `init_db`, assert column), `test_save_accepts_auto_pay`.

- [ ] **Step 2: Implement** per the interfaces. `poll_once` outline:

```python
    def poll_once(self, today=None):
        result = {'processed': 0, 'replied': 0, 'skipped': 0, 'reason': None}
        if self.client is None or not getattr(self.client, 'can_read', False):
            result['reason'] = 'bot token not configured'; return result
        now_ms = int(self.now().timestamp() * 1000)
        watermark = self.get_state('mm_last_post_create_at')
        if watermark is None:
            self.set_state('mm_last_post_create_at', str(now_ms)); result['reason'] = 'watermark initialised'; return result
        try:
            posts = self.client.posts_since(int(watermark))
            bot_id = self.client.me()['id']
        except MattermostError as exc:
            self._bump_failures(); result['reason'] = f'Mattermost error: {exc}'; return result
        self.set_state('mm_poll_failures', '0')
        allowed = self.allowed_user_ids({p['user_id'] for p in posts})
        sent = self._sent_keys(); newest = int(watermark)
        for post in posts:
            newest = max(newest, int(post['create_at']))
            if post['user_id'] == bot_id or post.get('type') or f"reply:{post['id']}" in sent or post['user_id'] not in allowed:
                result['skipped'] += 1; continue
            images = self._download_images(post)
            outcome = conversation.handle_post(self, post, self.settings, llm=self.llm, images=images, today=today or self.today())
            result['processed'] += 1
            if outcome.get('reply'):
                post_id = self.client.post(outcome['reply']) ...; log kind 'reply', dedupe f"reply:{post['id']}"; result['replied'] += 1
                if outcome.get('acted'): try add_reaction
        self.set_state('mm_last_post_create_at', str(newest)); self.set_state('mm_last_poll_at', self.now().isoformat()[:19])
        return result
```
`allowed_user_ids(ids)` resolves unknown ids through `client.users_by_ids`, caches id→username in `reminder_state` as JSON under `mm_user_cache`, and returns ids whose username is in `settings['allowed_users']` (from `mattermost.allowed_users`, passed into the service settings by `app.reminder_service` as `settings['allowed_users']`).

- [ ] **Step 3:** Suite green; commit `feat: channel polling, set-up questions, overdue and direct-debit handling`.

---

### Task 5: App wiring, routes, dashboard, docs

**Files:** `app.py`, `dashboard.html`, `README.md`, `docs/cash-flow-operations.md`, tests.

- `reminder_service()` passes `llm=llm_chat if llm_available() else None` and `settings['allowed_users']` from config `mattermost.allowed_users`.
- `POST /api/mattermost/poll` → `reminder_service().poll_once()`; `POST /api/mattermost/simulate {text}` → builds a fake post and calls `conversation.handle_post` with `dry_run` semantics (no DB writes: wrap in a transaction and roll back — implement by running against a copy? Simpler: `simulate` only supports parse + free-form answer preview: returns `{command: parse_command(...), reply: answer for free text}`; document that).
- `GET /api/mattermost/status` adds `last_poll_at`, `watermark`, `vision`.
- Dashboard: modal checkbox "Paid by direct debit" (`obl-auto-pay`), list shows "direct debit" tag; Conversation card renders kinds `reply` as "You → bot" pairs (body holds the bot reply; store the user's text in `reminder_log.body` prefixed `> user text\n\n`); Settings Mattermost block shows last poll time and "vision: Claude / OpenRouter / none".
- README: reply vocabulary table, aliases, direct debits, `ANTHROPIC_API_KEY` recommended for screenshot accuracy, `/api/mattermost/simulate`.
- Ops doc: "Replying in Mattermost" section.

- [ ] Tests: contract test for the checkbox and settings text; API tests for poll (mocked client) and simulate; then full suite green; commit `feat: two-way Mattermost wiring, dashboard and docs`.

---

### Task 6: Deploy and live verification

1. Merge to `main`, push, watch Coolify.
2. Confirm in the container: `can_read True`, `[reminders] ran …` and `poll` lines in logs, `mm_last_post_create_at` set.
3. Post `status` in the channel as Tyson; expect a position reply within 60 seconds with ✅.
4. Post `gst 13129`; expect "Got it".
5. Post a screenshot; expect balances read (OpenRouter quality caveat until an Anthropic key is set).
6. Ask a free-form question; expect a grounded answer.
7. Record outcomes in the Claude Log sheet.
